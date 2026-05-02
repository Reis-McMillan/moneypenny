import os

CLIENT_ID = os.environ.get('CLIENT_ID')
CLIENT_SECRET = os.environ.get('CLIENT_SECRET')
AUTH_URL = os.environ.get('AUTH_URL')
JWKS_URL = os.environ.get('JWKS_URL')
INIT_URI = 'https://api.moneypenny.mcmlln.dev/auth/initialize'
REDIRECT_URI = os.environ.get('REDIRECT_URI')
SCOPES = os.environ.get('SCOPES')
MCP_CLIENT_ID = os.environ.get('EMAIL_MCP_CLIENT_ID')
HOST = '0.0.0.0'
PORT = 8080
MONGO_URI = os.environ.get('MONGO_URI')
DB_NAME = 'moneypenny'
VLLM_CHAT_URL = os.environ.get('VLLM_CHAT_URL')
VLLM_EMBED_URL = os.environ.get('VLLM_EMBED_URL')
CHAT_MODEL = 'google/gemma-3-12b-it'
EMBEDDING_MODEL = 'google/embeddinggemma-300m'
FRONTEND_ORIGIN = os.environ.get('FRONTEND_ORIGIN')
MCP_URL = os.environ.get('MCP_URL')
EMAIL_CHECK_INTERVAL = 30 * 60 # 30 minutes (in seconds)
ALLOWED_ORIGINS = ['https://moneypenny.mcmlln.dev']
