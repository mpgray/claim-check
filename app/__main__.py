from app.config import ENV_FILE, load_env_file

# .env is read on every start, including each debug reload, and the reloader
# watches it: editing .env restarts the server with the new values.
load_env_file()

from app import create_app  # noqa: E402  (after the environment is set)

create_app().run(port=8050, debug=True, extra_files=[str(ENV_FILE)] if ENV_FILE.exists() else None)
