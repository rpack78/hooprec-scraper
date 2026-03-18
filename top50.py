import sqlite3

conn = sqlite3.connect('D:/dev/projects/hooprec-scraper/hooprec-ingest/players.db')
rows = conn.execute("""
    SELECT p.name, p.wins, p.losses, p.wins+p.losses AS total,
           ROUND(p.wins * 100.0 / NULLIF(p.wins+p.losses, 0), 1) AS win_pct,
           MAX(m.match_date) AS last_game
    FROM players p
    LEFT JOIN player_matches pm ON pm.player_id = p.id
    LEFT JOIN matches m ON m.id = pm.match_id
    WHERE p.wins + p.losses > 0 AND p.losses > 0
    GROUP BY p.id
    ORDER BY win_pct DESC, wins DESC, last_game DESC
    LIMIT 50
""").fetchall()

print(f"{'Rank':<5} {'Name':<28} {'W':>4} {'L':>4} {'GP':>4}  {'Win%':>6}  {'Last Game':<12}")
print("-" * 70)
for i, (name, w, l, total, pct, last_game) in enumerate(rows, 1):
    last = last_game if last_game else "N/A"
    print(f"{i:<5} {name:<28} {w:>4} {l:>4} {total:>4}  {pct:>5}%  {last:<12}")

conn.close()
