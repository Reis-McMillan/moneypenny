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
VLLM_CHAT_URL = os.environ.get('VLLM_CHAT_URL', 'http://192.168.1.72:8000/v1')
VLLM_EMBED_URL = os.environ.get('VLLM_EMBED_URL', 'http://192.168.1.72:8001/v1')
CHAT_MODEL = os.environ.get('CHAT_MODEL', 'google/gemma-3-12b-it')
EMBEDDING_MODEL = os.environ.get('EMBEDDING_MODEL', 'google/embeddinggemma-300m')
FRONTEND_ORIGIN = os.environ.get('FRONTEND_ORIGIN', 'http://localhost:5173')
MCP_URL = 'http://localhost:8000'
EMAIL_CHECK_INTERVAL = 30 * 60 # 30 minutes (in seconds)