"""Wires settings to AWS clients and the service.

AWS_ENDPOINT_URL (standard boto3 setting) points every client at LocalStack locally."""

import os

import boto3
from botocore.config import Config

from .artifacts import ArtifactStore
from .config import Settings
from .events import EventBridgePublisher, EventPublisher, LogPublisher
from .iam import Authenticator, HeaderAuthenticator, StsAuthenticator
from .repository import Repository
from .service import CompanyService


def build_service(settings: Settings) -> CompanyService:
    session = boto3.Session(region_name=settings.region)
    dynamodb = session.resource("dynamodb")
    # Emulators serve buckets by path, not by <bucket>.<host> DNS names.
    s3_config = Config(s3={"addressing_style": "path"}) if os.environ.get("AWS_ENDPOINT_URL") else None
    publisher: EventPublisher = (
        EventBridgePublisher(session.client("events"), settings.event_bus_name, settings.event_source)
        if settings.event_bus_name
        else LogPublisher()
    )
    return CompanyService(
        repo=Repository(dynamodb.Table(settings.table_name)),
        publisher=publisher,
        artifacts=ArtifactStore(session.client("s3", config=s3_config), settings.bucket),
    )


def build_authenticator(settings: Settings) -> Authenticator:
    if settings.auth_mode == "header":
        return HeaderAuthenticator()
    if settings.auth_mode != "iam":
        raise SystemExit(f"Unknown AUTH_MODE {settings.auth_mode!r}; use iam or header.")
    if not settings.agent_role_prefix:
        raise SystemExit("AGENT_ROLE_PREFIX is required when AUTH_MODE=iam.")
    account_id = settings.account_id or boto3.client("sts", region_name=settings.region).get_caller_identity()["Account"]
    return StsAuthenticator(
        region=settings.region,
        audience=settings.audience,
        role_prefix=settings.agent_role_prefix,
        account_id=account_id,
    )
