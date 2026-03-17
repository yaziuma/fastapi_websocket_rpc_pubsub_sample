from .app_config import load_node_config
from .node_app import create_app


app = create_app(load_node_config("server_b"))
