import yaml
import os

with open('local/analyst_list.txt', 'r', encoding='utf-8') as f:
    lines = f.readlines()

analysts = []
for line in lines:
    line = line.strip()
    if not line: continue
    
    parts = line.split(' https://www.youtube.com/')
    if len(parts) == 2:
        name = parts[0].strip()
        channel_info = parts[1].strip()
        channel = channel_info.split(' ')[0]
        
        analysts.append({
            'name': name,
            'channel': channel
        })

os.makedirs('config', exist_ok=True)
with open('config/analysts.yaml', 'w', encoding='utf-8') as f:
    yaml.dump({'analysts': analysts}, f, allow_unicode=True)

print("Created config/analysts.yaml")
