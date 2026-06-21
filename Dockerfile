FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /app/data

EXPOSE 8899

CMD ["python3", "-c", """
import subprocess, sys, signal, os

def cleanup(signum, frame):
    print('Received stop signal, exiting...')
    sys.exit(0)

signal.signal(signal.SIGTERM, cleanup)
signal.signal(signal.SIGINT, cleanup)

monitor = subprocess.Popen([sys.executable, 'main.py'],
                           stdout=sys.stdout, stderr=sys.stderr)

dashboard = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'api:app',
                              '--host', '0.0.0.0', '--port', '8899',
                              '--log-level', 'info'],
                             stdout=sys.stdout, stderr=sys.stderr)

print('ARK Live Monitor started')
print('Dashboard: http://0.0.0.0:8899')

try:
    ret = monitor.wait()
    print(f'Monitor exited(code={ret})')
    dashboard.terminate()
except KeyboardInterrupt:
    monitor.terminate()
    dashboard.terminate()
"""]
