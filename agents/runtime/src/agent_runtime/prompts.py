"""Loads the job description: AppConfig in AWS, a mounted file locally."""

from typing import Any

import boto3
import yaml

from .config import Settings


def load_prompt(settings: Settings) -> dict[str, Any]:
    if settings.prompt_source == "file":
        assert settings.prompt_file is not None
        return yaml.safe_load(settings.prompt_file.read_text())
    if settings.prompt_source != "appconfig":
        raise SystemExit(f"Unknown PROMPT_SOURCE {settings.prompt_source!r}; use appconfig or file.")

    client = boto3.client("appconfigdata", region_name=settings.region)
    session = client.start_configuration_session(
        ApplicationIdentifier=settings.prompt_application_id,
        EnvironmentIdentifier=settings.prompt_environment_id,
        ConfigurationProfileIdentifier=settings.prompt_profile_id,
    )
    response = client.get_latest_configuration(ConfigurationToken=session["InitialConfigurationToken"])
    return yaml.safe_load(response["Configuration"].read())
