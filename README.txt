# Painel de Tarefas COMPLETO (Calendário + Alerta do Admin)

## Rodar (Windows)
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
python server_tasks.py

Acesse: http://127.0.0.1:5000  (ou http://192.168.18.53:5000)
Login admin: admin / admin123

## O que tem
- Abre direto no Calendário
- Clique no dia para criar tarefa já naquela data/horário
- Emitir Alerta Manual no dashboard (admin): cria uma tarefa [ALERTA] atribuída ao usuário
- API:
  - /api/notify_tasks?username=Yasmin → retorna tarefas do usuário
  - /api/calendar_events → eventos para FullCalendar
- Usuários padrão: Yasmin, Hiasmin, Ana, Daniela (senha: 1234)
