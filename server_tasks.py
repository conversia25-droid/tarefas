# server_tasks.py – calendário, painel, emissão de alertas, APIs p/ extensão,
# SSE em tempo real (EventSource) e som via extensão (offscreen)

import os
import json
import datetime
from collections import defaultdict
from queue import Queue

from flask import (
    Flask, request, jsonify, render_template_string,
    redirect, url_for, session, abort, Response
)
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash

# =============================
# APP CONFIG
# =============================
app = Flask(__name__)
app.secret_key = os.environ.get("PANEL_SECRET", "trocar-isso-em-producao")

app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///painel_tarefas.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db = SQLAlchemy(app)

# =============================
# MODELS
# =============================
class User(db.Model):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), default="user")
    client_token = db.Column(db.String(100), nullable=True)
    host_id = db.Column(db.String(100), nullable=True)

    def check_password(self, pwd_plain):
        return check_password_hash(self.password_hash, pwd_plain)

class Task(db.Model):
    __tablename__ = "tasks"
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=True)

    assigned_to_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    assigned_to = db.relationship("User", backref="assigned_tasks", foreign_keys=[assigned_to_id])

    status = db.Column(db.String(30), default="pendente")
    active = db.Column(db.Boolean, default=True)

    due_date = db.Column(db.DateTime, nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)

class TaskLog(db.Model):
    __tablename__ = "task_logs"
    id = db.Column(db.Integer, primary_key=True)
    task_id = db.Column(db.Integer, db.ForeignKey("tasks.id"), nullable=False)
    host_id = db.Column(db.String(100), nullable=False)
    status = db.Column(db.String(20), default="pending")
    message = db.Column(db.Text, nullable=True)
    executed_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)

# =============================
# SIMPLE PUBSUB (SSE)
# =============================
subscribers = defaultdict(list)  # username -> [Queue, Queue, ...]

def sse_publish(username, event):
    """Envia um dicionário 'event' para todos inscritos no username via SSE."""
    for q in list(subscribers.get(username, [])):
        try:
            q.put_nowait(event)
        except Exception:
            pass

@app.route("/api/stream")
def api_stream():
    username = request.args.get("username", "").strip()
    if not username:
        return "username é obrigatório", 400

    user = User.query.filter_by(username=username).first()
    if not user:
        def _end():
            yield "event: error\ndata: {\"error\":\"user_not_found\"}\n\n"
        return Response(_end(), mimetype="text/event-stream")

    q = Queue()
    subscribers[username].append(q)

    def stream():
        yield 'event: hello\ndata: {"ok": true}\n\n'
        try:
            while True:
                ev = q.get()  # bloqueia até chegar algo
                yield f"event: task\ndata: {json.dumps(ev, default=str)}\n\n"
        except GeneratorExit:
            pass
        finally:
            try:
                subscribers[username].remove(q)
            except ValueError:
                pass

    headers = {
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
        "Access-Control-Allow-Origin": "*",
    }
    return Response(stream(), headers=headers, mimetype="text/event-stream")

# =============================
# DB INIT
# =============================
def create_or_update_user(username, password, role="user", host_id=None):
    u = User.query.filter_by(username=username).first()
    if u:
        u.password_hash = generate_password_hash(password)
        u.role = role
        u.host_id = host_id
    else:
        u = User(
            username=username,
            password_hash=generate_password_hash(password),
            role=role,
            host_id=host_id,
            client_token=None,
        )
        db.session.add(u)
    db.session.commit()
    return u

def init_db():
    db.create_all()
    # migração leve: garantir due_date existe em bancos antigos
    try:
        from sqlalchemy import text
        cols = db.session.execute(text("PRAGMA table_info(tasks)")).fetchall()
        names = [c[1] for c in cols]
        if "due_date" not in names:
            db.session.execute(text("ALTER TABLE tasks ADD COLUMN due_date TEXT"))
            db.session.commit()
    except Exception:
        pass

    # admin
    if not User.query.filter_by(username="admin").first():
        db.session.add(User(username="admin", password_hash=generate_password_hash("admin123"), role="admin"))
        db.session.commit()

    # usuários padrão
    for uname in ["Yasmin", "Hiasmin", "Ana", "Daniela"]:
        if not User.query.filter_by(username=uname).first():
            create_or_update_user(uname, "1234", role="user")

# =============================
# SESSION HELPERS
# =============================
def require_login():
    return "user_id" in session

def current_user():
    if "user_id" not in session:
        return None
    return db.session.get(User, session["user_id"])

def is_admin():
    u = current_user()
    return (u is not None and u.role == "admin")

# =============================
# LOGIN / HOME
# =============================
TPL_LOGIN = """<!DOCTYPE html>
<html lang="pt-BR"><head><meta charset="utf-8" /><meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Login • Painel de Tarefas</title>
<style>
  :root { --bg:#0f1b3a; --card:#1e2a4f; --accent:#4f6bff; --text:#fff; --dim:#9ba4d6; --r:14px; --p:24px; --font:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif; }
  body{ margin:0; min-height:100vh; background:radial-gradient(circle at 20% 20%,#1a2b6d 0%,#0f1b3a 60%); font-family:var(--font); color:var(--text); display:flex; align-items:center; justify-content:center; padding:24px; }
  .card{ background:var(--card); border-radius:var(--r); box-shadow:0 30px 80px rgba(0,0,0,.6); max-width:360px; width:100%; padding:var(--p); border:1px solid rgba(255,255,255,.07); }
  h2{ margin:0 0 4px; font-size:1.1rem; font-weight:600; } .subtitle{ font-size:.8rem; color:var(--dim); margin-bottom:20px; }
  .error{ background:rgba(255,83,83,.12); border:1px solid rgba(255,83,83,.4); color:#ff6b6b; font-size:.8rem; padding:8px 12px; border-radius:8px; margin-bottom:16px; }
  label{ display:block; font-size:.8rem; font-weight:500; margin-bottom:6px; }
  input{ width:100%; background:#0f1b3a; border:1px solid rgba(255,255,255,.12); border-radius:10px; padding:10px 12px; font-size:.85rem; color:#fff; outline:none; }
  input:focus{ border-color:var(--accent); box-shadow:0 0 0 3px rgba(79,107,255,.25); }
  .btn{ width:100%; background:linear-gradient(90deg,var(--accent) 0%,#6d89ff 100%); border:none; color:#fff; font-size:.9rem; font-weight:600; padding:11px 14px; border-radius:10px; cursor:pointer; box-shadow:0 16px 40px rgba(79,107,255,.4); margin-top:8px; }
  .footer{ margin-top:14px; font-size:.7rem; text-align:center; color:var(--dim); }
</style></head>
<body>
  <div class="card">
    <h2>Entrar no Painel</h2>
    <div class="subtitle">Gestão de tarefas</div>
    {% if error %}<div class="error">{{error}}</div>{% endif %}
    <form method="post">
      <label>Usuário</label><input name="username" autocomplete="username" />
      <div style="height:10px"></div>
      <label>Senha</label><input name="password" type="password" autocomplete="current-password" />
      <button class="btn" type="submit">Entrar</button>
    </form>
    <div class="footer">Precisa de acesso? Fale com o admin.</div>
  </div>
</body></html>
"""

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        usr = request.form.get("username", "").strip()
        pwd = request.form.get("password", "").strip()
        u = User.query.filter_by(username=usr).first()
        if u and u.check_password(pwd):
            session["user_id"] = u.id
            return redirect(url_for("calendar_view"))
        return render_template_string(TPL_LOGIN, error="Login incorreto")
    return render_template_string(TPL_LOGIN, error=None)

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.route("/")
def home():
    if not require_login():
        return redirect(url_for("login"))
    return redirect(url_for("calendar_view"))

# =============================
# DASHBOARD ADMIN
# =============================
TPL_DASHBOARD = """<!DOCTYPE html>
<html lang="pt-BR"><head><meta charset="utf-8" /><meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Painel • Tarefas</title>
<style>
  :root { --bg:#0f1b3a; --panel:#1a254a; --card:#1e2a4f; --stroke:#2f3a6d; --accent:#4f6bff; --text:#fff; --dim:#9ba4d6; --r-xl:20px; --r:12px; --font:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif; }
  body{ margin:0; background:radial-gradient(circle at 20% 20%,#1a2b6d 0%,#0f1b3a 60%); color:var(--text); font-family:var(--font); min-height:100vh; display:flex; flex-direction:column; }
  .topbar{ background:linear-gradient(90deg,#1e2a4f 0%,#2a3770 100%); border-bottom:1px solid rgba(255,255,255,.07); box-shadow:0 30px 80px rgba(0,0,0,.6);
    padding:16px 24px; border-radius:0 0 var(--r-xl) var(--r-xl); display:flex; flex-wrap:wrap; align-items:flex-start; justify-content:space-between; row-gap:12px; }
  .title{ margin:0; font-size:1rem; font-weight:600; } .subtitle{ font-size:.75rem; color:var(--dim); margin-top:4px; }
  .session-box{ background:rgba(15,27,58,.6); border:1px solid rgba(255,255,255,.07); border-radius:var(--r); padding:12px 16px; font-size:.75rem; color:var(--dim); }
  .session-box b{color:var(--text);} .logout-link{ color:var(--accent); font-weight:500; text-decoration:none; }
  .main{ flex:1; padding:24px; display:grid; grid-template-columns:380px 1fr; gap:24px; }
  .card{ background:var(--card); border-radius:var(--r); border:1px solid rgba(255,255,255,.07); box-shadow:0 24px 60px rgba(0,0,0,.6); padding:16px 20px; }
  label{ display:block; font-size:.7rem; font-weight:500; margin-bottom:6px; }
  input, textarea, select{ width:100%; background:#0f1b3a; border:1px solid rgba(255,255,255,.12); border-radius:10px; padding:9px 11px; font-size:.8rem; color:#fff; outline:none; }
  textarea{min-height:80px;resize:vertical;} input:focus, textarea:focus, select:focus{ border-color:var(--accent); box-shadow:0 0 0 3px rgba(79,107,255,.18); }
  .btn{ background:linear-gradient(90deg,var(--accent) 0%,#6d89ff 100%); border:none; color:#fff; font-size:.8rem; font-weight:600; padding:9px 12px; border-radius:10px; cursor:pointer; box-shadow:0 16px 40px rgba(79,107,255,.4); }
  .table-wrap{ overflow:auto; border:1px solid rgba(255,255,255,.07); border-radius:10px; background:#0f1b3a; box-shadow:0 16px 40px rgba(0,0,0,.7); }
  table{ width:100%; border-collapse:collapse; min-width:760px; font-size:.77rem; color:#fff; }
  th{ text-align:left; background:#1a254a; color:var(--dim); font-weight:500; padding:10px 12px; border-bottom:1px solid var(--stroke); white-space:nowrap; }
  td{ padding:10px 12px; border-bottom:1px solid rgba(255,255,255,.05); vertical-align:top; }
  .pill{background:rgba(255,255,255,.06);padding:6px 8px;border-radius:8px;font-size:.75rem;}
  .small{font-size:.75rem;color:#9ba4d6;}
</style></head>
<body>
  <header class="topbar">
    <div>
      <p class="title">Painel de Tarefas</p>
      <p class="subtitle">Administre tarefas e atribuições</p>
    </div>
    <div class="session-box">
      <div>Usuário: <b>{{user.username}}</b> ({{user.role}})</div>
      <div style="margin-top:6px;">
        <a class="logout-link" href="{{url_for('calendar_view')}}">Calendário</a> ·
        <a class="logout-link" href="{{url_for('logout')}}">Sair</a>
      </div>
    </div>
  </header>

  <main class="main">
    <section>
      <div class="card">
        <h3 style="margin-top:0">Emitir Alerta Manual</h3>
        <form method="post" action="{{url_for('emit_alert')}}">
          <label>Atribuir a</label>
          <select name="assigned_to_id" required>
            {% for u in users %}<option value="{{u.id}}">{{u.username}}</option>{% endfor %}
          </select>
          <label style="margin-top:10px;">Título do alerta</label>
          <input name="title" placeholder="Ex.: Reunião agora" required />
          <label style="margin-top:10px;">Mensagem (opcional)</label>
          <textarea name="description" placeholder="Detalhes do alerta"></textarea>
          <div style="height:12px"></div>
          <button class="btn" type="submit">Emitir alerta</button>
        </form>
      </div>

      <div class="card" style="margin-top:16px;">
        <h3 style="margin-top:0">Criar nova tarefa</h3>
        <form method="post" action="{{url_for('create_task')}}">
          <label>Título</label><input name="title" required />
          <label style="margin-top:10px;">Descrição</label><textarea name="description"></textarea>
          <label style="margin-top:10px;">Atribuir a</label>
          <select name="assigned_to_id">
            <option value="">-- (não atribuída) --</option>
            {% for u in users %}<option value="{{u.id}}">{{u.username}}</option>{% endfor %}
          </select>
          <label style="margin-top:10px;">Status</label>
          <select name="status">
            <option value="pendente">pendente</option>
            <option value="em_andamento">em andamento</option>
            <option value="concluida">concluída</option>
          </select>
          <label style="margin-top:10px;">Data de vencimento</label>
          <input type="datetime-local" name="due_date" />
          <div style="height:12px"></div>
          <button class="btn" type="submit">Criar</button>
        </form>
      </div>
    </section>

    <section>
      <div class="card">
        <h3 style="margin-top:0">Tarefas</h3>
        <div class="table-wrap">
          <table>
            <tr>
              <th>ID</th><th>Título</th><th>Descrição</th><th>Atribuído</th><th>Status</th><th>Vencimento</th><th>Criada</th><th></th>
            </tr>
            {% for t in tasks %}
            <tr>
              <td>#{{t.id}}</td>
              <td>{{t.title}}</td>
              <td style="white-space:pre-wrap;">{{t.description or '-'}}</td>
              <td>{{t.assigned_to.username if t.assigned_to else '-'}}</td>
              <td><span class="pill">{{t.status}}</span></td>
              <td class="small">{{t.due_date or '-'}}</td>
              <td class="small">{{t.created_at}}</td>
              <td>
                <a class="btn" href="{{url_for('edit_task_form', task_id=t.id)}}">Editar</a>
                <form method="post" action="{{url_for('delete_task', task_id=t.id)}}" style="display:inline" onsubmit="return confirm('Excluir tarefa #{{t.id}}?');">
                  <button class="btn" style="background:transparent;border:1px solid rgba(255,255,255,.2)">Excluir</button>
                </form>
              </td>
            </tr>
            {% endfor %}
          </table>
        </div>
      </div>
      <div class="card" style="margin-top:16px;">
        <h3 style="margin-top:0">Usuários</h3>
        <div class="table-wrap" style="margin-top:12px;">
          <table>
            <tr><th>ID</th><th>Usuário</th><th>Role</th><th>Host</th></tr>
            {% for u in users %}
              <tr><td>{{u.id}}</td><td>{{u.username}}</td><td class="small">{{u.role}}</td><td class="small">{{u.host_id or '-'}}</td></tr>
            {% endfor %}
          </table>
        </div>
      </div>
    </section>
  </main>
</body></html>
"""

@app.route("/dashboard")
def dashboard():
    if not require_login():
        return redirect(url_for("login"))
    if not is_admin():
        return redirect(url_for("calendar_view"))
    tasks = Task.query.order_by(Task.created_at.desc()).all()
    users_list = User.query.order_by(User.username.asc()).all()
    return render_template_string(TPL_DASHBOARD, user=current_user(), tasks=tasks, users=users_list)

# cria tarefa
@app.route("/task/create", methods=["POST"])
def create_task():
    if not require_login():
        abort(403)
    u = current_user()
    title = request.form.get("title", "").strip()
    description = request.form.get("description", "").strip()
    status = request.form.get("status", "pendente").strip()
    due_date_raw = request.form.get("due_date", "").strip()
    assigned_to_id = request.form.get("assigned_to_id", "").strip() if is_admin() else str(u.id)

    if not title:
        return "Título obrigatório", 400

    due_dt = None
    if due_date_raw:
        try:
            due_dt = datetime.datetime.strptime(due_date_raw, "%Y-%m-%dT%H:%M")
        except ValueError:
            due_dt = None

    t = Task(
        title=title,
        description=description or None,
        status=status or "pendente",
        active=True,
        due_date=due_dt,
    )

    if assigned_to_id and assigned_to_id.isdigit():
        ass = db.session.get(User, int(assigned_to_id))
        if ass:
            t.assigned_to = ass

    db.session.add(t)
    db.session.commit()

    # SSE: notifica atribuída (se houver)
    if t.assigned_to:
        sse_publish(t.assigned_to.username, {
            "type": "created",
            "task": {
                "id": t.id, "title": t.title, "description": t.description or "",
                "status": t.status, "due_date": t.due_date.isoformat() if t.due_date else None
            }
        })

    return redirect(url_for("calendar_view"))

# ALERTA RÁPIDO DO ADMIN
@app.route("/admin/emit_alert", methods=["POST"])
def emit_alert():
    if not require_login() or not is_admin():
        abort(403)
    title = request.form.get("title", "").strip()
    description = request.form.get("description", "").strip()
    assigned_to_id = request.form.get("assigned_to_id", "").strip()
    if not (title and assigned_to_id.isdigit()):
        return "Dados inválidos", 400

    ass = db.session.get(User, int(assigned_to_id))
    if not ass:
        return "Usuário inválido", 400

    now = datetime.datetime.utcnow()
    t = Task(
        title=f"Urgente: {title}",
        description=description or None,
        assigned_to=ass,
        status="pendente",
        active=True,
        due_date=now
    )
    db.session.add(t)
    db.session.commit()

    # SSE: empurra alerta
    sse_publish(ass.username, {
        "type": "alert",
        "task": {
            "id": t.id, "title": t.title, "description": t.description or "",
            "status": t.status, "due_date": t.due_date.isoformat() if t.due_date else None
        }
    })

    return redirect(url_for("dashboard"))

# editar
@app.route("/task/<int:task_id>/edit", methods=["GET"])
def edit_task_form(task_id):
    if not require_login():
        abort(403)
    t = db.session.get(Task, task_id)
    if not t:
        abort(404)
    users_list = User.query.order_by(User.username.asc()).all() if is_admin() else []
    TPL_EDIT = """<!DOCTYPE html>
<html lang="pt-BR"><head><meta charset="utf-8" /><meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Editar Tarefa</title>
<style>
  :root{ --card:#1e2a4f; --accent:#4f6bff; --text:#fff; --dim:#9ba4d6; --font:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif; }
  body{background:radial-gradient(circle at 20% 20%,#1a2b6d 0%,#0f1b3a 60%);font-family:var(--font);color:var(--text);padding:24px;}
  .card{max-width:880px;margin:0 auto;background:var(--card);padding:18px;border-radius:12px;border:1px solid rgba(255,255,255,.06);}
  label{display:block;color:var(--dim);margin-top:10px;}
  input,textarea,select{width:100%;padding:8px;border-radius:8px;border:1px solid rgba(255,255,255,.08);background:#0f1b3a;color:var(--text);}
  .btn{margin-top:12px;background:linear-gradient(90deg,var(--accent) 0%,#6d89ff 100%);padding:10px 14px;border-radius:10px;border:none;color:#fff;font-weight:600;}
  a.back{color:var(--dim);display:inline-block;margin-top:12px;text-decoration:none;}
</style></head>
<body>
  <div class="card">
    <h2>Editar tarefa #{{task.id}}</h2>
    <form method="post" action="{{url_for('edit_task', task_id=task.id)}}">
      <label>Título</label><input name="title" value="{{task.title}}" required />
      <label>Descrição</label><textarea name="description">{{task.description}}</textarea>
      {% if is_admin %}
      <label>Atribuir a</label>
      <select name="assigned_to_id">
        <option value="">-- (não atribuída) --</option>
        {% for u in users %}
          <option value="{{u.id}}" {% if task.assigned_to and task.assigned_to.id == u.id %}selected{% endif %}>{{u.username}}</option>
        {% endfor %}
      </select>
      {% endif %}
      <label>Status</label>
      <select name="status">
        <option value="pendente" {% if task.status=='pendente' %}selected{% endif %}>pendente</option>
        <option value="em_andamento" {% if task.status=='em_andamento' %}selected{% endif %}>em andamento</option>
        <option value="concluida" {% if task.status=='concluida' %}selected{% endif %}>concluída</option>
      </select>
      <label>Data de vencimento</label>
      <input type="datetime-local" name="due_date" value="{{ (task.due_date.isoformat()[:16]) if task.due_date else '' }}" />
      <div style="margin-top:10px;">
        <button class="btn" type="submit">Salvar alterações</button>
        <a class="back" href="{{url_for('calendar_view')}}">← Voltar</a>
      </div>
    </form>
  </div>
</body></html>
"""
    return render_template_string(TPL_EDIT, task=t, users=users_list, is_admin=is_admin())

@app.route("/task/<int:task_id>/edit", methods=["POST"])
def edit_task(task_id):
    if not require_login():
        abort(403)
    t = db.session.get(Task, task_id)
    if not t:
        abort(404)

    title = request.form.get("title", "").strip()
    description = request.form.get("description", "").strip()
    status = request.form.get("status", "pendente").strip()
    due_date_raw = request.form.get("due_date", "").strip()

    if not title:
        return "Título obrigatório", 400

    t.title = title
    t.description = description or None
    t.status = status or "pendente"

    if due_date_raw:
        try:
            t.due_date = datetime.datetime.strptime(due_date_raw, "%Y-%m-%dT%H:%M")
        except ValueError:
            t.due_date = None
    else:
        t.due_date = None

    if is_admin():
        assigned_to_id = request.form.get("assigned_to_id", "").strip()
        if assigned_to_id:
            if assigned_to_id.isdigit():
                ass = db.session.get(User, int(assigned_to_id))
                t.assigned_to = ass
            else:
                t.assigned_to = None

    db.session.commit()

    # SSE: avisa o usuário atual da tarefa
    if t.assigned_to:
        sse_publish(t.assigned_to.username, {
            "type": "updated",
            "task": {
                "id": t.id, "title": t.title, "description": t.description or "",
                "status": t.status, "due_date": t.due_date.isoformat() if t.due_date else None
            }
        })

    return redirect(url_for("calendar_view"))

@app.route("/task/<int:task_id>/delete", methods=["POST"])
def delete_task(task_id):
    if not require_login() or not is_admin():
        abort(403)
    t = db.session.get(Task, task_id)
    if not t:
        abort(404)
    db.session.delete(t)
    db.session.commit()
    return redirect(url_for("dashboard"))

# =============================
# USER LISTA / CONCLUI
# =============================
@app.route("/user_tasks")
def user_tasks():
    if not require_login():
        return redirect(url_for("login"))
    u = current_user()
    tasks = Task.query.filter(
        Task.active.is_(True),
        Task.assigned_to_id == u.id
    ).order_by(Task.created_at.desc()).all()

    TPL_USER = """<!DOCTYPE html>
<html lang="pt-BR"><head><meta charset="utf-8" /><meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Minhas Tarefas</title>
<style>
  :root { --bg:#0f1b3a; --card:#1e2a4f; --accent:#4f6bff; --text:#fff; --dim:#9ba4d6; --r:12px; --font:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif; }
  body{ margin:0; min-height:100vh; background:radial-gradient(circle at 20% 20%,#1a2b6d 0%,#0f1b3a 60%); font-family:var(--font); color:#fff; display:flex; flex-direction:column; }
  header{ background:linear-gradient(90deg,#1e2a4f 0%,#2a3770 100%); padding:16px 24px; border-radius:0 0 20px 20px; border-bottom:1px solid rgba(255,255,255,.07);
    box-shadow:0 30px 80px rgba(0,0,0,.6); display:flex; justify-content:space-between; font-size:.8rem; }
  .user-info{color:#9ba4d6;} .user-info b{color:#fff;} .logout a{color:#4f6bff;text-decoration:none;}
  main{flex:1;padding:24px;max-width:880px;width:100%;margin:0 auto;display:flex;flex-direction:column;gap:18px;}
  .card{background:#1e2a4f;border-radius:12px;border:1px solid rgba(255,255,255,.07);box-shadow:0 24px 60px rgba(0,0,0,.6);padding:16px 20px;}
  h2{margin:0 0 6px;font-size:1rem;font-weight:600;} .subtitle{font-size:.75rem;color:#9ba4d6;margin-bottom:12px;}
  .task{background:#0f1b3a;border:1px solid rgba(255,255,255,.12);border-radius:10px;padding:12px 14px;margin-bottom:12px;}
  .task-title{font-size:.9rem;font-weight:600;margin:0 0 6px;}
  .task-desc{font-size:.8rem;line-height:1.4;color:#9ba4d6;white-space:pre-wrap;margin:0 0 8px;}
  .task-footer{display:flex;justify-content:space-between;align-items:center;font-size:.8rem;color:#9ba4d6;}
  .btn{background:linear-gradient(90deg,#4f6bff 0%,#6d89ff 100%);border:none;color:#fff;padding:8px 12px;border-radius:8px;cursor:pointer;font-weight:600;}
</style></head>
<body>
  <header>
    <div class="user-info">
      <div>Usuário: <b>{{user.username}}</b> ({{user.role}})</div>
      <div>Estas são suas tarefas atribuídas.</div>
    </div>
    <div class="logout"><a href="{{url_for('logout')}}">Sair</a></div>
  </header>
  <main>
    <div class="card">
      <h2>Minhas Tarefas</h2>
      <div class="subtitle">Marque como concluída quando terminar.</div>
      {% if tasks %}
        {% for t in tasks %}
          <div class="task">
            <div class="task-title">#{{t.id}} · {{t.title}} <span class="small">({{t.status}})</span></div>
            <div class="task-desc">{{t.description or '-'}}</div>
            <div class="task-footer">
              <div>Venc: {{t.due_date or '-'}}</div>
              {% if t.status != 'concluida' %}
              <form method="post" action="{{url_for('mark_task_complete')}}">
                <input type="hidden" name="task_id" value="{{t.id}}" />
                <button class="btn" type="submit">✔ Concluir</button>
              </form>
              {% else %}
              <div>Concluída</div>
              {% endif %}
            </div>
          </div>
        {% endfor %}
      {% else %}
        <div style="text-align:center;color:#9ba4d6;">Nenhuma tarefa pendente 👌</div>
      {% endif %}
    </div>
  </main>
</body></html>
"""
    return render_template_string(TPL_USER, user=u, tasks=tasks)

@app.route("/task/complete", methods=["POST"])
def mark_task_complete():
    if not require_login():
        return redirect(url_for("login"))
    u = current_user()
    task_id_raw = request.form.get("task_id", "").strip()
    if not task_id_raw.isdigit():
        return "task_id inválido", 400
    t = db.session.get(Task, int(task_id_raw))
    if not t:
        return "Tarefa não encontrada", 404
    if t.assigned_to_id != u.id and not is_admin():
        return "Não autorizado", 403

    t.status = "concluida"
    db.session.commit()

    lg = TaskLog(task_id=t.id, host_id=u.username, status="done_manual", message="Concluída via painel")
    db.session.add(lg); db.session.commit()

    # SSE: completed
    sse_publish(u.username, {"type": "completed", "task": {"id": t.id}})

    return redirect(url_for("user_tasks"))

# =============================
# API: EXTENSÃO
# =============================
@app.route("/api/notify_tasks")
def api_notify_tasks():
    username = request.args.get("username", "").strip()
    if not username:
        return jsonify({"error": "username ausente"}), 400

    user = User.query.filter_by(username=username).first()
    if not user:
        return jsonify([])

    tasks = Task.query.filter(
        Task.active.is_(True),
        Task.assigned_to_id == user.id
    ).order_by(Task.created_at.desc()).all()

    return jsonify([{
        "id": t.id,
        "title": t.title,
        "description": t.description or "",
        "status": t.status,
        "created_at": t.created_at.isoformat() if t.created_at else "",
        "due_date": t.due_date.isoformat() if t.due_date else None
    } for t in tasks])

@app.route("/api/mark_complete", methods=["POST"])
def api_mark_complete():
    expected = os.environ.get("PANEL_PUBLIC_TOKEN")  # opcional
    token = request.headers.get("X-Panel-Token") or request.args.get("token")
    if expected and token != expected:
        return jsonify({"error": "forbidden"}), 403

    username = request.form.get("username", "").strip()
    task_id = request.form.get("task_id", "").strip()

    if not (username and task_id.isdigit()):
        return jsonify({"error": "bad_request"}), 400

    user = User.query.filter_by(username=username).first()
    if not user:
        return jsonify({"error": "user_not_found"}), 404

    t = Task.query.filter_by(id=int(task_id), assigned_to_id=user.id).first()
    if not t:
        return jsonify({"error": "task_not_found"}), 404

    t.status = "concluida"
    db.session.commit()

    lg = TaskLog(task_id=t.id, host_id=username, status="done_api", message="Concluída via API pública")
    db.session.add(lg); db.session.commit()

    # SSE: completed
    sse_publish(username, {"type": "completed", "task": {"id": t.id}})

    return jsonify({"ok": True, "task_id": t.id})

# =============================
# CALENDÁRIO
# =============================
@app.route("/api/calendar_events")
def api_calendar_events():
    q = Task.query.filter(Task.active.is_(True))

    assigned_name = request.args.get("assigned_to", "").strip()
    if assigned_name:
        u = User.query.filter_by(username=assigned_name).first()
        if u:
            q = q.filter(Task.assigned_to_id == u.id)
        else:
            return jsonify([])

    start = request.args.get("start", "").strip()
    end = request.args.get("end", "").strip()
    if start and end:
        try:
            s = datetime.datetime.fromisoformat(start)
            e = datetime.datetime.fromisoformat(end)
            q = q.filter(Task.due_date.isnot(None), Task.due_date >= s, Task.due_date <= e)
        except Exception:
            pass

    tasks = q.order_by(Task.due_date.asc().nulls_last()).all()
    events = []
    for t in tasks:
        if t.due_date:
            events.append({
                "id": t.id,
                "title": f"{t.title} ({t.assigned_to.username if t.assigned_to else '-'})",
                "start": t.due_date.isoformat(),
                "url": url_for("edit_task_form", task_id=t.id)
            })
    return jsonify(events)

@app.route("/calendar")
def calendar_view():
    if not require_login():
        return redirect(url_for("login"))

    u = current_user()
    admin = is_admin()
    username_filter = None if admin else (u.username if u else None)
    users_list = User.query.order_by(User.username.asc()).all() if admin else []

    def user_options():
        if not admin:
            return '<option value="">(somente suas tarefas)</option>'
        opts = ['<option value="">-- (não atribuída) --</option>']
        for usr in users_list:
            opts.append(f'<option value="{usr.id}">{usr.username}</option>')
        return "\n".join(opts)

    html = f"""<!DOCTYPE html>
<html lang="pt-BR"><head><meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Calendário • Tarefas</title>
<link href="https://cdn.jsdelivr.net/npm/fullcalendar@6.1.15/main.min.css" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/fullcalendar@6.1.15/main.min.js"></script>
<style>
  body{{margin:0;background:radial-gradient(circle at 20% 20%,#1a2b6d 0%,#0f1b3a 60%);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;color:#fff;}}
  header{{background:linear-gradient(90deg,#1e2a4f 0%,#2a3770 100%);padding:14px 20px;border-radius:0 0 20px 20px;border-bottom:1px solid rgba(255,255,255,.07);box-shadow:0 30px 80px rgba(0,0,0,.6);display:flex;justify-content:space-between;align-items:center}}
  .title{{font-weight:600;}} .links a{{color:#9ba4d6;text-decoration:none;margin-left:12px}}
  #calendar{{max-width:1100px;margin:20px auto;background:#0f1b3a;border:1px solid rgba(255,255,255,.1);border-radius:12px;box-shadow:0 24px 60px rgba(0,0,0,.6);padding:10px}}
  .modal-backdrop{{position:fixed;inset:0;background:rgba(0,0,0,.5);display:none;align-items:center;justify-content:center;z-index:9999}}
  .modal{{width:94%;max-width:520px;background:#1e2a4f;border:1px solid rgba(255,255,255,.1);border-radius:12px;box-shadow:0 24px 60px rgba(0,0,0,.6);padding:16px}}
  .modal h3{{margin:0 0 10px}}
  label{{display:block;font-size:.8rem;color:#9ba4d6;margin-top:8px}}
  input, textarea, select{{width:100%;background:#0f1b3a;border:1px solid rgba(255,255,255,.12);border-radius:10px;padding:9px 11px;font-size:.85rem;color:#fff;outline:none}}
  textarea{{min-height:80px;resize:vertical}}
  .actions{{display:flex;gap:8px;justify-content:flex-end;margin-top:12px}}
  .btn{{background:linear-gradient(90deg,#4f6bff 0%,#6d89ff 100%);border:none;color:#fff;padding:9px 12px;border-radius:10px;font-weight:600;cursor:pointer}}
  .btn.ghost{{background:transparent;border:1px solid rgba(255,255,255,.25);color:#cbd1ff}}
  .top-right{{position:fixed;right:18px;top:12px;color:#9ba4d6;font-size:.85rem}}
</style>
</head>
<body>
<header>
  <div class="title">Calendário de Tarefas</div>
  <div class="links">
    <span class="top-right">Usuário: <b>{u.username if u else '-'}</b> ({u.role if u else '-'})</span>
    {"<a href='" + url_for('dashboard') + "'>Painel</a> ·" if admin else ""}
    <a href="{url_for('logout')}">Sair</a>
  </div>
</header>

<div id="calendar"></div>

<div class="modal-backdrop" id="backdrop">
  <div class="modal">
    <h3>Criar tarefa</h3>
    <form id="createForm">
      <label>Título</label>
      <input name="title" id="f_title" required />
      <label>Descrição</label>
      <textarea name="description" id="f_desc"></textarea>
      {"<label>Atribuir a</label><select name='assigned_to_id' id='f_user'>" + user_options() + "</select>" if admin else ""}
      <label>Status</label>
      <select name="status" id="f_status">
        <option value="pendente">pendente</option>
        <option value="em_andamento">em andamento</option>
        <option value="concluida">concluída</option>
      </select>
      <label>Data e hora</label>
      <input type="datetime-local" name="due_date" id="f_due" required />
      <div class="actions">
        <button type="button" class="btn ghost" id="cancelBtn">Cancelar</button>
        <button type="submit" class="btn">Criar</button>
      </div>
    </form>
  </div>
</div>

<script>
(function() {{
  const admin = {str(admin).lower()};
  const usernameFilter = {json.dumps(username_filter)};

  function toLocalInputValue(iso) {{
    const d = new Date(iso);
    const p = n => n<10 ? "0"+n : ""+n;
    return `${{d.getFullYear()}}-${{p(d.getMonth()+1)}}-${{p(d.getDate())}}T${{p(d.getHours())}}:${{p(d.getMinutes())}}`;
  }}

  const el = document.getElementById('calendar');
  const calendar = new FullCalendar.Calendar(el, {{
    initialView: 'dayGridMonth',
    headerToolbar: {{
      left: 'prev,next today',
      center: 'title',
      right: 'dayGridMonth,timeGridWeek,timeGridDay,listWeek'
    }},
    navLinks: true,
    selectable: true,
    editable: false,
    eventTimeFormat: {{ hour: '2-digit', minute: '2-digit', meridiem: false }},
    events: function(info, success, failure) {{
      const params = new URLSearchParams();
      params.set('start', info.startStr);
      params.set('end', info.endStr);
      if (usernameFilter) params.set('assigned_to', usernameFilter);
      fetch('/api/calendar_events?' + params.toString(), {{ cache: 'no-store' }})
        .then(r => r.json()).then(data => success(data))
        .catch(err => failure(err));
    }},
    dateClick: function(info) {{
      openModal(info.dateStr);
    }},
    eventClick: function(info) {{
      if (info.event.url) {{
        window.open(info.event.url, '_blank');
        info.jsEvent.preventDefault();
      }}
    }}
  }});
  calendar.render();

  const backdrop = document.getElementById('backdrop');
  const fTitle = document.getElementById('f_title');
  const fDesc  = document.getElementById('f_desc');
  const fDue   = document.getElementById('f_due');
  const fForm  = document.getElementById('createForm');
  const cancelBtn = document.getElementById('cancelBtn');
  const fUser = document.getElementById('f_user'); // null se não admin
  const fStatus = document.getElementById('f_status');

  function openModal(dayStr) {{
    const base = new Date(dayStr);
    base.setHours(9,0,0,0);
    fDue.value = toLocalInputValue(base.toISOString());
    fTitle.value = "";
    fDesc.value = "";
    if (fUser) fUser.value = "";
    fStatus.value = "pendente";
    backdrop.style.display = "flex";
    setTimeout(()=>fTitle.focus(), 50);
  }}
  function closeModal(){{ backdrop.style.display = "none"; }}
  cancelBtn.addEventListener('click', closeModal);
  backdrop.addEventListener('click', (e) => {{ if (e.target === backdrop) closeModal(); }});

  fForm.addEventListener('submit', async (e) => {{
    e.preventDefault();
    const data = new FormData(fForm);
    try {{
      const resp = await fetch('/task/create', {{ method: 'POST', body: data }});
      if (!resp.ok) throw new Error('Falha ao criar tarefa');
      closeModal();
      calendar.refetchEvents();
    }} catch(err) {{
      alert('Erro: ' + err.message);
    }}
  }});
}})();
</script>
</body></html>"""
    return html

# =============================
# HEALTH / UTILS
# =============================
@app.route("/health")
def health():
    return jsonify({"status": "ok"}), 200

@app.after_request
def add_cors_headers(resp):
    resp.headers['Access-Control-Allow-Origin'] = '*'
    resp.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization'
    resp.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
    return resp

@app.errorhandler(500)
def internal_error(e):
    import traceback
    traceback.print_exc()
    return "Erro interno no servidor (500). Veja o terminal.", 500

# =============================
# MAIN
# =============================
if __name__ == "__main__":
    with app.app_context():
        init_db()
    app.run(host="0.0.0.0", port=5000, debug=False, use_reloader=False)
