import os

CLIENT_ID = os.environ.get('CLIENT_ID')
CLIENT_SECRET = os.environ.get('CLIENT_SECRET')
AUTH_URL = os.environ.get('AUTH_URL')
JWKS_URL = os.environ.get('JWKS_URL')
REDIRECT_URI = os.environ.get('REDIRECT_URI')
SCOPES = os.environ.get('SCOPES', 'openid email mcp google microsoft')
MCP_CLIENT_ID = os.environ.get('MCP_CLIENT_ID')
HOST = os.environ.get('HOST', 'localhost')
PORT = int(os.environ.get('PORT', 8080))
MONGO_URI = 'mongodb://localhost:27017'
DB_NAME = 'moneypenny'
OLLAMA_HOST = 'http://192.168.1.72:11434'
MCP_URL = 'http://localhost:8000'
EMAIL_CHECK_INTERVAL = 30 * 60 # 30 minutes (in seconds)