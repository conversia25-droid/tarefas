# server_tasks.py – Painel/Tarefas com Calendário + SSE + APIs
# Flask + SQLAlchemy (SQLite em /instance)

import os
import json
import datetime
from collections import defaultdict
from queue import Queue

from flask import (
    Flask, request, jsonify, render_template_string, render_template,
    redirect, url_for, session, abort, Response
)
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash

# =============================
# APP + DB (usa /instance)
# =============================
app = Flask(__name__, instance_relative_config=True)
os.makedirs(app.instance_path, exist_ok=True)

DB_FILENAME = os.environ.get("PANEL_DB_FILENAME", "painel_tarefas.db")
db_path = os.path.join(app.instance_path, DB_FILENAME)
app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{db_path}"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

app.secret_key = os.environ.get("PANEL_SECRET", "trocar-isso-em-producao")
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

    status = db.Column(db.String(30), default="pendente")  # pendente, em_andamento, concluida
    active = db.Column(db.Boolean, default=True)

    due_date = db.Column(db.DateTime, nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)

class TaskLog(db.Model):
    __tablename__ = "task_logs"
    id = db.Column(db.Integer, primary_key=True)
    task_id = db.Column(db.Integer, db.ForeignKey("tasks.id"), nullable=False)
    host_id = db.Column(db.String(100), nullable=False)
    status = db.Column(db.String(20), default="pending") # observation_web, observation, done_*
    message = db.Column(db.Text, nullable=True)
    executed_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)

# =============================
# SIMPLE PUBSUB (SSE)
# =============================
subscribers = defaultdict(list)  # username -> [Queue, ...]

def sse_publish(username, event):
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
    if username != "admin" and not user:
        def _end():
            yield "event: error\ndata: {\"error\":\"user_not_found\"}\n\n"
        return Response(_end(), mimetype="text/event-stream")

    q = Queue()
    subscribers[username].append(q)

    def stream():
        yield 'event: hello\ndata: {"ok": true}\n\n'
        try:
            while True:
                ev = q.get()
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
# DB INIT / MIGRAÇÃO LEVE
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

def _ensure_column(table, colname, decl_sqlite):
    try:
        from sqlalchemy import text
        cols = db.session.execute(text(f"PRAGMA table_info({table})")).fetchall()
        names = [c[1] for c in cols]
        if colname not in names:
            db.session.execute(text(f"ALTER TABLE {table} ADD COLUMN {decl_sqlite}"))
            db.session.commit()
    except Exception:
        pass

def init_db():
    db.create_all()
    _ensure_column("tasks", "description", "description TEXT")
    _ensure_column("tasks", "due_date", "due_date TEXT")
    if not User.query.filter_by(username="admin").first():
        db.session.add(User(username="admin", password_hash=generate_password_hash("admin123"), role="admin"))
        db.session.commit()
    for uname in ["Yasmin", "Hiasmin", "Ana", "Daniela"]:
        if not User.query.filter_by(username=uname).first():
            create_or_update_user(uname, "1234", role="user")

# =============================
# HELPERS (SESSÃO)
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
# TEMA BÁSICO (LOGIN/DASH/USER)
# =============================
THEME_CSS = """
:root[data-theme="light"]{
  --bg:#ffffff; --surface:#ffffff; --surface-2:#f7f8fa;
  --text:#111; --muted:#666; --stroke:#e8eaee; --shadow:rgba(0,0,0,.06);
  --button:#111; --button-text:#fff; --chip:#f2f4f7; --chip-text:#333;
}
:root[data-theme="dark"]{
  --bg:#0b0f14; --surface:#11161d; --surface-2:#0f141b;
  --text:#fff; --muted:#9ba4b0; --stroke:#1f2733; --shadow:rgba(0,0,0,.5);
  --button:#e6e6e6; --button-text:#111; --chip:#1b2330; --chip-text:#d7dfeb;
}
html,body{height:100%}
body{margin:0;background:var(--bg);color:var(--text);
  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
  transition:background .2s ease,color .2s ease;}
.container{max-width:1160px;margin:24px auto 40px;padding:0 16px;}
.topbar{display:flex;gap:12px;align-items:center;justify-content:space-between;flex-wrap:wrap;
  background:var(--surface);border:1px solid var(--stroke);border-radius:12px;padding:12px 16px;
  box-shadow:0 10px 30px var(--shadow);}
.title{font-weight:700;letter-spacing:.2px}
.top-right{display:flex;gap:12px;align-items:center;flex-wrap:wrap;color:var(--muted);font-size:.9rem}
.top-right b{color:var(--text)}
.top-links a{color:var(--muted);text-decoration:none;border:1px solid var(--stroke);padding:6px 10px;border-radius:10px}
.btn-theme{border:1px solid var(--stroke);background:var(--surface-2);color:var(--text);padding:8px 12px;border-radius:10px;font-weight:600;cursor:pointer}
.card{background:var(--surface);border:1px solid var(--stroke);border-radius:12px;box-shadow:0 10px 30px var(--shadow);padding:16px}
label{display:block;font-size:.8rem;color:var(--muted);margin-bottom:6px}
input,textarea,select{width:100%;background:var(--surface-2);border:1px solid var(--stroke);border-radius:10px;padding:10px 12px;font-size:.9rem;color:var(--text);outline:none}
input:focus,textarea:focus,select:focus{box-shadow:0 0 0 3px rgba(100,150,250,.18)}
.btn{background:var(--button);color:var(--button-text);border:none;border-radius:10px;padding:10px 12px;font-weight:700;cursor:pointer}
.table-wrap{overflow:auto;border:1px solid var(--stroke);border-radius:10px;background:var(--surface-2)}
table{width:100%;border-collapse:collapse;min-width:760px}
th{background:var(--surface-2);color:var(--muted);font-weight:600;padding:10px 12px;border-bottom:1px solid var(--stroke);text-align:left;white-space:nowrap}
td{padding:10px 12px;border-bottom:1px solid var(--stroke);vertical-align:top}
.pill{background:var(--chip);color:var(--chip-text);padding:6px 8px;border-radius:8px;font-size:.8rem}
.small{font-size:.8rem;color:var(--muted)}
#calendar{background:var(--surface);border:1px solid var(--stroke);border-radius:12px;box-shadow:0 10px 30px var(--shadow);padding:10px}
"""

THEME_JS = """
<script>
(function(){
  const root = document.documentElement;
  const current = localStorage.getItem('theme') || 'light';
  root.setAttribute('data-theme', current);
  function labelFor(theme){ return theme==='dark' ? 'Light' : 'Black'; }
  function iconFor(theme){ return theme==='dark' ? '☀️' : '🌙'; }
  function updateBtn(btn){
    const t = root.getAttribute('data-theme');
    btn.querySelector('.icon').textContent = iconFor(t);
    btn.querySelector('.label').textContent = labelFor(t);
  }
  window.applyThemeToggle = function(buttonId){
    const btn = document.getElementById(buttonId);
    if(!btn) return;
    updateBtn(btn);
    btn.addEventListener('click',()=>{
      const next = root.getAttribute('data-theme')==='light' ? 'dark' : 'light';
      root.setAttribute('data-theme', next);
      localStorage.setItem('theme', next);
      updateBtn(btn);
    });
  }
})();
</script>
"""

# =============================
# LOGIN
# =============================
TPL_LOGIN = """<!DOCTYPE html>
<html lang="pt-BR" data-theme="light"><head><meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Login • Painel de Tarefas</title>
<style>{{ theme_css|safe }}</style>
{{ theme_js|safe }}
</head>
<body>
  <div class="container">
    <div class="topbar">
      <div class="title">Entrar no Painel</div>
      <button id="toggleLogin" class="btn-theme"><span class="icon">🌙</span> <span class="label">Black</span></button>
    </div>
    <div class="card" style="max-width:420px;margin:20px auto;">
      <div class="small" style="margin-bottom:8px;">Use seu usuário e senha</div>
      {% if error %}<div class="pill" style="background:#ffd6d6;color:#b10000;">{{error}}</div>{% endif %}
      <form method="post" style="margin-top:10px;">
        <label>Usuário</label><input name="username" autocomplete="username" />
        <label>Senha</label><input name="password" type="password" autocomplete="current-password" />
        <div style="height:10px"></div>
        <button class="btn" type="submit">Entrar</button>
      </form>
    </div>
  </div>
  <script>applyThemeToggle('toggleLogin');</script>
</body></html>
"""

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        usr = request.form.get("username", "").strip()
        pwd = request.form.get("password", "").strip()
        search_username = usr.strip()
        if search_username.lower() != 'admin':
            search_username = search_username.capitalize()
        u = User.query.filter_by(username=search_username).first()
        if u and u.check_password(pwd):
            session["user_id"] = u.id
            return redirect(url_for("calendar_view"))
        return render_template_string(TPL_LOGIN, error="Login incorreto", theme_css=THEME_CSS, theme_js=THEME_JS)
    return render_template_string(TPL_LOGIN, error=None, theme_css=THEME_CSS, theme_js=THEME_JS)

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
# DASHBOARD (ADMIN)
# =============================
TPL_DASHBOARD = """<!DOCTYPE html>
<html lang="pt-BR"><head><meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Painel • Tarefas</title>
<style>{{ theme_css|safe }}</style>
{{ theme_js|safe }}
</head>
<body>
  <div class="container">
    <div class="topbar">
      <div>
        <div class="title">Painel de Tarefas</div>
        <div class="small">Administre tarefas e atribuições</div>
      </div>
      <div class="top-right">
        <div>Usuário: <b>{{user.username}}</b> ({{user.role}})</div>
        <div class="top-links">
          <a href="{{url_for('calendar_view')}}">Calendário</a>
          <a href="{{url_for('logout')}}">Sair</a>
        </div>
        <button id="toggleDash" class="btn-theme"><span class="icon">🌙</span> <span class="label">Black</span></button>
      </div>
    </div>

    <main style="display:grid;grid-template-columns:380px 1fr;gap:24px;margin-top:16px;">
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
              <option value="pendente">Pendente</option>
              <option value="em_andamento">Em andamento</option>
              <option value="concluida">Concluída</option>
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
                    <button class="btn" style="background:transparent;color:var(--text);border:1px solid var(--stroke)">Excluir</button>
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
  </div>
  <script>applyThemeToggle('toggleDash');</script>
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
    return render_template_string(
        TPL_DASHBOARD,
        user=current_user(),
        tasks=tasks,
        users=users_list,
        theme_css=THEME_CSS,
        theme_js=THEME_JS
    )

# cria tarefa (Painel/Calendário)
@app.route("/task/create", methods=["POST"])
def create_task():
    if not require_login():
        abort(403)
    u = current_user()
    title = request.form.get("title", "").strip()
    description = request.form.get("description", "").strip()
    status = request.form.get("status", "pendente").strip()
    due_date_raw = request.form.get("due_date", "").strip()

    # admin pode escolher; usuário comum é atribuído a si mesmo
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

    if t.assigned_to:
        sse_publish(t.assigned_to.username, {
            "type": "created",
            "task": {
                "id": t.id, "title": t.title, "description": t.description or "",
                "status": t.status, "due_date": t.due_date.isoformat() if t.due_date else None
            }
        })
    sse_publish("admin", {"type": "changed"})

    # Se veio do calendário (modal com fetch), redirecionar de volta ao calendário
    # (o frontend trata com fetch e refetchEvents; manter redirect funciona para submit tradicional)
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
        title=f"[ALERTA] {title}",
        description=description or None,
        assigned_to=ass,
        status="pendente",
        active=True,
        due_date=now
    )
    db.session.add(t)
    db.session.commit()

    sse_publish(ass.username, {
        "type": "alert",
        "task": {
            "id": t.id, "title": t.title, "description": t.description or "",
            "status": t.status, "due_date": t.due_date.isoformat() if t.due_date else None
        }
    })
    sse_publish("admin", {"type": "changed"})

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
    task_logs = TaskLog.query.filter_by(task_id=task_id).order_by(TaskLog.executed_at.desc()).all()
    
    TPL_EDIT = """<!DOCTYPE html>
<html lang="pt-BR"><head><meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Editar Tarefa</title>
<style>{{ theme_css|safe }}</style>
{{ theme_js|safe }}
</head>
<body>
  <div class="container">
    <div class="topbar">
      <div class="title">Editar tarefa #{{task.id}}</div>
      <div class="top-right">
        <a class="top-links" href="{{url_for('calendar_view')}}">← Voltar</a>
        <button id="toggleEdit" class="btn-theme"><span class="icon">🌙</span> <span class="label">Black</span></button>
      </div>
    </div>

    <div class="card" style="max-width:880px;margin:16px auto;">
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
          <option value="pendente" {% if task.status=='pendente' %}selected{% endif %}>Pendente</option>
          <option value="em_andamento" {% if task.status=='em_andamento' %}selected{% endif %}>Em andamento</option>
          <option value="concluida" {% if task.status=='concluida' %}selected{% endif %}>Concluída</option>
        </select>
        <label>Data de vencimento</label>
        <input type="datetime-local" name="due_date" value="{{ (task.due_date.isoformat()[:16]) if task.due_date else '' }}" />
        
        <div style="margin-top:12px;display:flex;gap:8px;justify-content:flex-end;">
          <button class="btn" type="submit">Salvar alterações</button>
        </div>
      </form>
      
      <hr style="border-color:var(--stroke); margin: 20px 0;" />
      
      <h4 style="margin-top:0;">Adicionar Observação (Interação)</h4>
      <form method="post" action="{{url_for('add_task_log', task_id=task.id)}}">
          <label>Comentário</label>
          <textarea name="message" required placeholder="Digite sua observação ou resposta..."></textarea>
          <div style="margin-top:10px; text-align:right;">
              <button class="btn" style="background:#5cb85c;" type="submit">Enviar Comentário</button>
          </div>
      </form>

      <h4 style="margin-top:20px;">Histórico de Observações/Logs</h4>
      {% for log in task_logs %}
          <div style="font-size:0.9rem; margin-bottom: 6px; border-left: 3px solid {{ 'var(--muted)' if log.status.startswith('observation') else 'var(--stroke)' }}; padding-left: 8px;">
              <span class="small">{{ log.executed_at.strftime('%d/%m %H:%M') }} por <b>{{ log.host_id }}</b> ({{ log.status.replace('observation_web', 'Comentário Web').replace('observation', 'Comentário Ext.') }}):</span>
              <div style="white-space: pre-wrap;">{{ log.message }}</div>
          </div>
      {% endfor %}
      {% if not task_logs %}<div class="small">Sem histórico.</div>{% endif %}
      
      <div style="margin-top:12px;display:flex;gap:8px;justify-content:flex-end;">
          <a class="top-links" href="{{url_for('calendar_view')}}" style="display:inline-block;padding:10px 12px;border-radius:10px;border:1px solid var(--stroke);text-decoration:none;">← Voltar ao Calendário</a>
      </div>
    </div>
  </div>
  <script>applyThemeToggle('toggleEdit');</script>
</body></html>
"""
    return render_template_string(
        TPL_EDIT,
        task=t,
        users=users_list,
        is_admin=is_admin(),
        task_logs=task_logs,
        theme_css=THEME_CSS,
        theme_js=THEME_JS
    )

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

    if t.assigned_to:
        sse_publish(t.assigned_to.username, {
            "type": "updated",
            "task": {
                "id": t.id, "title": t.title, "description": t.description or "",
                "status": t.status, "due_date": t.due_date.isoformat() if t.due_date else None
            }
        })
    sse_publish("admin", {"type": "changed"})

    return redirect(url_for("calendar_view"))

# =============================
# OBSERVAÇÕES / LOGS
# =============================
@app.route("/task/<int:task_id>/add_log", methods=["POST"])
def add_task_log(task_id):
    if not require_login():
        abort(403)
    u = current_user()
    t = db.session.get(Task, task_id)
    message = request.form.get("message", "").strip()

    if not (t and message):
        return "Dados inválidos", 400
    
    lg = TaskLog(task_id=t.id, host_id=u.username, status="observation_web", message=message)
    db.session.add(lg)
    db.session.commit()
    
    target_username = t.assigned_to.username if t.assigned_to else "admin"
    sse_publish(target_username, {"type": "new_observation", "task_id": t.id, "message": message, "user": u.username})
    if target_username != "admin":
        sse_publish("admin", {"type": "new_observation", "task_id": t.id, "message": message, "user": u.username})

    payload = {
        "id": t.id,
        "title": f"[OBS] Nova interação na tarefa #{t.id}",
        "description": (message or "")[:280],
        "status": t.status,
        "due_date": t.due_date.isoformat() if t.due_date else None
    }
    sse_publish(target_username, {"type": "alert", "task": payload})

    return redirect(url_for("edit_task_form", task_id=task_id))

# API: lista logs por tarefa (para popup ver histórico)
@app.route("/api/task_logs")
def api_task_logs():
    username = request.args.get("username", "").strip()
    task_id = request.args.get("task_id", "").strip()
    if not (username and task_id.isdigit()):
        return jsonify({"error": "bad_request"}), 400

    search_username = username if username.lower() == "admin" else username.capitalize()
    user = User.query.filter_by(username=search_username).first()
    if not user:
        return jsonify({"error": "user_not_found"}), 404

    t = db.session.get(Task, int(task_id))
    if not t:
        return jsonify({"error": "task_not_found"}), 404

    if user.role != "admin" and (t.assigned_to_id != user.id):
        return jsonify({"error": "forbidden"}), 403

    logs = TaskLog.query.filter_by(task_id=t.id).order_by(TaskLog.executed_at.asc()).all()
    return jsonify([{
        "id": lg.id,
        "task_id": lg.task_id,
        "by": lg.host_id,
        "status": lg.status,
        "message": lg.message or "",
        "at": lg.executed_at.isoformat()
    } for lg in logs])

@app.route("/task/<int:task_id>/delete", methods=["POST"])
def delete_task(task_id):
    if not require_login() or not is_admin():
        abort(403)
    t = db.session.get(Task, task_id)
    if not t:
        abort(404)
    db.session.delete(t)
    db.session.commit()
    sse_publish("admin", {"type": "changed"})
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

    def format_status(status):
        if status == 'pendente':
            return 'Pendente'
        elif status == 'em_andamento':
            return 'Em andamento'
        elif status == 'concluida':
            return 'Concluída'
        return status

    TPL_USER = """<!DOCTYPE html>
<html lang="pt-BR"><head><meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Minhas Tarefas</title>
<style>{{ theme_css|safe }}</style>
{{ theme_js|safe }}
</head>
<body>
  <div class="container">
    <div class="topbar">
      <div>
        <div class="title">Minhas Tarefas</div>
        <div class="small">Marque como concluída quando terminar.</div>
      </div>
      <div class="top-right">
        <div>Usuário: <b>{{user.username}}</b> ({{user.role}})</div>
        <div class="top-links"><a href="{{url_for('logout')}}">Sair</a></div>
        <button id="toggleUser" class="btn-theme"><span class="icon">🌙</span> <span class="label">Black</span></button>
      </div>
    </div>

    <div class="card" style="max-width:920px;margin:16px auto;">
      {% if tasks %}
        {% for t in tasks %}
          <div class="card" style="margin-bottom:12px;background:var(--surface-2)">
            <div style="display:flex;justify-content:space-between;gap:12px;align-items:center;">
              <div style="font-weight:700">#{{t.id}} · {{t.title}} <span class="small">({{format_status(t.status)}})</span></div>
              {% if t.status != 'concluida' %}
              <form method="post" action="{{url_for('mark_task_complete')}}">
                <input type="hidden" name="task_id" value="{{t.id}}" />
                <button class="btn" type="submit">✔ Concluir</button>
              </form>
              {% else %}
              <div class="small">Concluída</div>
              {% endif %}
            </div>
            <div class="small" style="white-space:pre-wrap;margin-top:6px;">{{t.description or '-'}}</div>
            <div class="small" style="margin-top:6px;">Venc: {{t.due_date or '-'}}</div>
          </div>
        {% endfor %}
      {% else %}
        <div style="text-align:center;color:var(--muted);">Nenhuma tarefa pendente 👌</div>
      {% endif %}
    </div>
  </div>
  <script>applyThemeToggle('toggleUser');</script>
</body></html>
"""
    return render_template_string(
        TPL_USER, user=u, tasks=tasks, format_status=format_status,
        theme_css=THEME_CSS, theme_js=THEME_JS
    )

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

    sse_publish(u.username, {"type": "completed", "task": {"id": t.id}})
    sse_publish("admin", {"type": "changed"})

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

    sse_publish(username, {"type": "completed", "task": {"id": t.id}})
    sse_publish("admin", {"type": "changed"})

    return jsonify({"ok": True, "task_id": t.id})

# API: Adiciona uma observação/log à tarefa (via Extensão)
@app.route("/api/add_observation", methods=["POST"])
def api_add_observation():
    expected = os.environ.get("PANEL_PUBLIC_TOKEN") # opcional
    token = request.headers.get("X-Panel-Token") or request.args.get("token")
    if expected and token != expected:
        return jsonify({"error": "forbidden"}), 403

    username = request.form.get("username", "").strip()
    task_id = request.form.get("task_id", "").strip()
    message = request.form.get("message", "").strip()

    if not (username and task_id.isdigit() and message):
        return jsonify({"error": "bad_request", "details": "username, task_id ou message ausente"}), 400

    search_username = username.strip()
    if search_username.lower() != 'admin':
        search_username = search_username.capitalize()
        
    user = User.query.filter_by(username=search_username).first()
    if not user:
        return jsonify({"error": "user_not_found"}), 404

    t = db.session.get(Task, int(task_id))
    if not t:
        return jsonify({"error": "task_not_found"}), 404
        
    lg = TaskLog(task_id=t.id, host_id=user.username, status="observation", message=message)
    db.session.add(lg)
    db.session.commit()
    
    target_username = t.assigned_to.username if t.assigned_to else "admin"
    sse_publish(target_username, {"type": "new_observation", "task_id": t.id, "message": message, "user": user.username})
    if target_username != "admin":
        sse_publish("admin", {"type": "new_observation", "task_id": t.id, "message": message, "user": user.username})

    payload = {
        "id": t.id,
        "title": f"[OBS] Nova interação na tarefa #{t.id}",
        "description": (message or "")[:280],
        "status": t.status,
        "due_date": t.due_date.isoformat() if t.due_date else None
    }
    sse_publish(target_username, {"type": "alert", "task": payload})

    return jsonify({"ok": True, "task_id": t.id})

# =============================
# CALENDÁRIO
# =============================
def _parse_iso_flex(s: str):
    if not s:
        return None
    s = s.strip()
    try:
        if s.endswith("Z"):
            s = s.replace("Z", "+00:00")
        return datetime.datetime.fromisoformat(s)
    except Exception:
        try:
            if "T" in s:
                s = s.split("T", 1)[0]
            return datetime.datetime.strptime(s, "%Y-%m-%d")
        except Exception:
            return None

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

    s_dt = _parse_iso_flex(start)
    e_dt = _parse_iso_flex(end)

    if s_dt and e_dt:
        q = q.filter(Task.due_date.isnot(None), Task.due_date >= s_dt, Task.due_date <= e_dt)
    else:
        q = q.filter(Task.due_date.isnot(None))

    tasks = q.order_by(Task.due_date.asc().nulls_last()).all()

    events = []
    for t in tasks:
        if not t.due_date:
            continue
        events.append({
            "id": t.id,
            "title": f"{t.title} ({t.assigned_to.username if t.assigned_to else '-'})",
            "start": t.due_date.strftime("%Y-%m-%dT%H:%M:%S"),
            "url": url_for("edit_task_form", task_id=t.id),
            "status": t.status,
        })
    return jsonify(events)

@app.route("/calendar")
def calendar_view():
    if not require_login():
        return redirect(url_for("login"))
    u = current_user()
    admin = is_admin()
    users_list = User.query.order_by(User.username.asc()).all() if admin else []
    # Renderiza template externo em templates/calendar.html
    return render_template("calendar.html", user=u, is_admin=admin, users=users_list)

# =============================
# HEALTH / UTILS
# =============================
@app.route("/health")
def health():
    return jsonify({"status": "ok"}), 200

@app.after_request
def add_cors_headers(resp):
    resp.headers['Access-Control-Allow-Origin'] = '*'
    resp.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization, X-Panel-Token'
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
    print(f"DB em: {db_path}")
    app.run(host="0.0.0.0", port=5000, debug=False, use_reloader=False)
