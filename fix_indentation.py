import re
with open("src/tw_analyst_pipeline/youtube/fetcher.py", "r") as f:
    text = f.read()

# Fix indentations issues from replacement scripts and ensure python compiles
try:
    compile(text, "src/tw_analyst_pipeline/youtube/fetcher.py", "exec")
    print("Syntax OK")
except Exception as e:
    print("Syntax Error:", e)
