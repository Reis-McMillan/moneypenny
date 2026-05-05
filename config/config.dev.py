import os

CLIENT_ID = os.environ.get('MONEYPENNY_CLIENT_ID')
CLIENT_SECRET = os.environ.get('MONEYPENNY_CLIENT_SECRET')
AUTH_URL = os.environ.get('AUTH_URL')
JWKS_URL = os.environ.get('JWKS_URL')
INIT_URI = 'http://localhost:8080/auth/initialize'
REDIRECT_URI = 'http://localhost:8080/auth/callback'
SCOPES = 'openid email google'
MCP_CLIENT_ID = os.environ.get('EMAIL_MCP_CLIENT_ID')
HOST = '0.0.0.0'
PORT = 8080
MONGO_URI = 'mongodb://localhost:27017?directConnection=true'
DB_NAME = 'moneypenny'
VLLM_CHAT_URL = os.environ.get('VLLM_CHAT_URL', 'http://192.168.1.72:8000/v1')
VLLM_EMBED_URL = os.environ.get('VLLM_EMBED_URL', 'http://192.168.1.72:8001/v1')
CHAT_MODEL = 'google/gemma-4-E4B-it'
EMBEDDING_MODEL = 'google/embeddinggemma-300m'
FRONTEND_ORIGIN = 'http://localhost:5173'
MCP_URL = 'http://localhost:8000'
EMAIL_CHECK_INTERVAL = 30 * 60 # 30 minutes (in seconds)
REDIS_URL = os.environ.get('REDIS_URL', 'redis://localhost:6379/0')
ALLOWED_ORIGINS = ['http://localhost:5173']
