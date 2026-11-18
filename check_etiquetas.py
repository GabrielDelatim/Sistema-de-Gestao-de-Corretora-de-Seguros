import sqlite3, os, json

db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'corretora.db')
conn = sqlite3.connect(db_path)
cur = conn.cursor()
cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE '%etiqueta%';")
rows = cur.fetchall()
print(json.dumps(rows))
