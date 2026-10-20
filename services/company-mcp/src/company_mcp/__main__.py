from .app import build_authenticator, build_service
from .config import Settings
from .logs import configure_logging
from .server import create_server


def main() -> None:
    settings = Settings.from_env()
    configure_logging(settings.log_level)
    server = create_server(build_service(settings), build_authenticator(settings))
    # Stateless HTTP: any MCP task can serve any request, so tasks are replaceable.
    server.run(
        transport="http",
        host=settings.host,
        port=settings.port,
        path="/mcp",
        stateless_http=True,
        show_banner=False,
    )


if __name__ == "__main__":
    main()
