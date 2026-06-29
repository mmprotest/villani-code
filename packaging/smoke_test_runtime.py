import json, subprocess, sys
exe=sys.argv[1]
p=subprocess.Popen([exe,'bridge','--stdio'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True)
assert p.stdout and p.stdin
ready=json.loads(p.stdout.readline()); assert ready=={"type":"ready","protocol_version":1}
p.stdin.write('{"type":"ping","id":"release-smoke"}\n'); p.stdin.flush()
pong=json.loads(p.stdout.readline()); assert pong=={"type":"pong","id":"release-smoke"}
p.kill()
