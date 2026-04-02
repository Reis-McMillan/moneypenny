import os

CLIENT_ID = os.environ.get('CLIENT_ID')
CLIENT_SECRET = os.environ.get('CLIENT_SECRET')
HOST = ''
PORT = ''
MONGO_URI = 'mongodb://localhost:27017'
DB_NAME = 'jmail'
CREDS_PATH = '/home/reis/.google/client_creds.json'
TOKEN_PATH = '/home/reis/.google/app_tokens.json'
OLLAMA_HOST = 'http://192.168.1.72:11434'
MCP_URL = 'http://localhost:8000'