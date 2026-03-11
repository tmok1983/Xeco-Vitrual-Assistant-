from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from app.core.config import AppConfig

SCOPES = [
    "https://www.googleapis.com/auth/presentations",
    "https://www.googleapis.com/auth/drive",
]


def _state_file(config: AppConfig) -> Path:
    return Path(config.google_oauth_token_file or "credentials/google-oauth-token.json").with_name("google-oauth-state.json")


def _token_file(config: AppConfig) -> Path:
    return Path(config.google_oauth_token_file or "credentials/google-oauth-token.json")


def _load_user_credentials(config: AppConfig):
    if not config.google_oauth_enabled or not config.google_oauth_client_secret_file:
        return None

    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
    except ImportError:
        return None

    token_file = _token_file(config)
    if not token_file.exists():
        return None

    creds = Credentials.from_authorized_user_file(str(token_file), SCOPES)
    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            token_file.write_text(creds.to_json(), encoding="utf-8")
        except Exception:
            return None
    return creds if creds.valid else None


def _load_service_account_credentials(config: AppConfig):
    if not config.google_slides_enabled or not config.google_service_account_file:
        return None

    try:
        from google.oauth2 import service_account
    except ImportError:
        return None

    return service_account.Credentials.from_service_account_file(
        config.google_service_account_file,
        scopes=SCOPES,
    )


def get_google_oauth_status(config: AppConfig) -> dict[str, Any]:
    creds = _load_user_credentials(config)
    return {
        "oauth_enabled": config.google_oauth_enabled,
        "configured": bool(config.google_oauth_client_secret_file),
        "connected": bool(creds),
        "redirect_uri": config.google_oauth_redirect_uri,
    }


def build_google_oauth_url(config: AppConfig) -> str | None:
    if not config.google_oauth_enabled or not config.google_oauth_client_secret_file:
        return None

    try:
        from google_auth_oauthlib.flow import Flow
    except ImportError:
        return None

    flow = Flow.from_client_secrets_file(
        config.google_oauth_client_secret_file,
        scopes=SCOPES,
    )
    flow.redirect_uri = config.google_oauth_redirect_uri
    auth_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )
    state_file = _state_file(config)
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(
        __import__("json").dumps(
            {
                "state": state,
                "code_verifier": getattr(flow, "code_verifier", None),
            }
        ),
        encoding="utf-8",
    )
    return auth_url


def handle_google_oauth_callback(config: AppConfig, code: str, state: str | None) -> dict[str, Any]:
    if not config.google_oauth_enabled or not config.google_oauth_client_secret_file:
        raise ValueError("Google OAuth is not configured")

    try:
        from google_auth_oauthlib.flow import Flow
    except ImportError as exc:
        raise ValueError("google-auth-oauthlib is not installed") from exc

    state_file = _state_file(config)
    payload = {}
    if state_file.exists():
        try:
            payload = __import__("json").loads(state_file.read_text(encoding="utf-8"))
        except Exception:
            payload = {"state": state_file.read_text(encoding="utf-8").strip()}
    expected_state = payload.get("state")
    if expected_state and state and state != expected_state:
        raise ValueError("OAuth state mismatch")

    flow = Flow.from_client_secrets_file(
        config.google_oauth_client_secret_file,
        scopes=SCOPES,
        state=expected_state or state,
    )
    flow.redirect_uri = config.google_oauth_redirect_uri
    if payload.get("code_verifier"):
        flow.code_verifier = payload["code_verifier"]
    flow.fetch_token(code=code)
    creds = flow.credentials

    token_file = _token_file(config)
    token_file.parent.mkdir(parents=True, exist_ok=True)
    token_file.write_text(creds.to_json(), encoding="utf-8")

    if state_file.exists():
        state_file.unlink()

    return {
        "connected": True,
        "account_hint": getattr(creds, "account", None),
    }


def _build_services(credentials):
    from googleapiclient.discovery import build

    drive_service = build("drive", "v3", credentials=credentials, cache_discovery=False)
    slides_service = build("slides", "v1", credentials=credentials, cache_discovery=False)
    return drive_service, slides_service


def _create_presentation_file(title: str, config: AppConfig, drive_service, slides_service) -> tuple[str | None, dict[str, Any]]:
    if config.google_slides_folder_id:
        file_body: dict[str, Any] = {
            "name": title,
            "mimeType": "application/vnd.google-apps.presentation",
            "parents": [config.google_slides_folder_id],
        }
        supports_all_drives = _is_shared_drive_folder(config.google_slides_folder_id, drive_service)
        presentation = drive_service.files().create(
            body=file_body,
            fields="id",
            supportsAllDrives=supports_all_drives,
        ).execute()
        return presentation["id"], {"supports_all_drives": supports_all_drives}

    presentation = slides_service.presentations().create(body={"title": title}).execute()
    return presentation["presentationId"], {"supports_all_drives": False}


def _is_shared_drive_folder(folder_id: str, drive_service) -> bool:
    try:
        meta = drive_service.files().get(
            fileId=folder_id,
            fields="driveId",
            supportsAllDrives=True,
        ).execute()
    except Exception:
        return False
    return bool(meta.get("driveId"))


def _build_slide_requests(pages: list[dict[str, str]]) -> list[dict[str, Any]]:
    requests: list[dict[str, Any]] = []
    for index, page in enumerate(pages):
        slide_id = f"slide_{index}"
        accent_id = f"accent_{index}"
        section_id = f"section_{index}"
        title_id = f"title_{index}"
        key_id = f"key_{index}"
        body_id = f"body_{index}"
        reco_id = f"reco_{index}"
        visual_icon_id = f"visual_icon_{index}"
        footer_left_id = f"footer_left_{index}"
        footer_right_id = f"footer_right_{index}"
        requests.extend(
            [
                {"createSlide": {"objectId": slide_id, "insertionIndex": index, "slideLayoutReference": {"predefinedLayout": "BLANK"}}},
                {
                    "createShape": {
                        "objectId": accent_id,
                        "shapeType": "RECTANGLE",
                        "elementProperties": {
                            "pageObjectId": slide_id,
                            "size": {"width": {"magnitude": 720, "unit": "PT"}, "height": {"magnitude": 28, "unit": "PT"}},
                            "transform": {"scaleX": 1, "scaleY": 1, "translateX": 0, "translateY": 0, "unit": "PT"},
                        },
                    }
                },
                {
                    "updateShapeProperties": {
                        "objectId": accent_id,
                        "shapeProperties": {
                            "shapeBackgroundFill": {
                                "solidFill": {
                                    "color": {"rgbColor": {"red": 0.71, "green": 0.33, "blue": 0.04}},
                                    "alpha": 1,
                                }
                            },
                            "outline": {"propertyState": "NOT_RENDERED"},
                        },
                        "fields": "shapeBackgroundFill.solidFill.color,shapeBackgroundFill.solidFill.alpha,outline.propertyState",
                    }
                },
                {
                    "createShape": {
                        "objectId": section_id,
                        "shapeType": "TEXT_BOX",
                        "elementProperties": {
                            "pageObjectId": slide_id,
                            "size": {"width": {"magnitude": 180, "unit": "PT"}, "height": {"magnitude": 24, "unit": "PT"}},
                            "transform": {"scaleX": 1, "scaleY": 1, "translateX": 40, "translateY": 40, "unit": "PT"},
                        },
                    }
                },
                {
                    "createShape": {
                        "objectId": title_id,
                        "shapeType": "TEXT_BOX",
                        "elementProperties": {
                            "pageObjectId": slide_id,
                            "size": {"width": {"magnitude": 620, "unit": "PT"}, "height": {"magnitude": 54, "unit": "PT"}},
                            "transform": {"scaleX": 1, "scaleY": 1, "translateX": 40, "translateY": 68, "unit": "PT"},
                        },
                    }
                },
                {
                    "createShape": {
                        "objectId": key_id,
                        "shapeType": "TEXT_BOX",
                        "elementProperties": {
                            "pageObjectId": slide_id,
                            "size": {"width": {"magnitude": 620, "unit": "PT"}, "height": {"magnitude": 58, "unit": "PT"}},
                            "transform": {"scaleX": 1, "scaleY": 1, "translateX": 40, "translateY": 128, "unit": "PT"},
                        },
                    }
                },
                {
                    "createShape": {
                        "objectId": body_id,
                        "shapeType": "TEXT_BOX",
                        "elementProperties": {
                            "pageObjectId": slide_id,
                            "size": {"width": {"magnitude": 350, "unit": "PT"}, "height": {"magnitude": 150, "unit": "PT"}},
                            "transform": {"scaleX": 1, "scaleY": 1, "translateX": 40, "translateY": 220, "unit": "PT"},
                        },
                    }
                },
                {
                    "createShape": {
                        "objectId": reco_id,
                        "shapeType": "TEXT_BOX",
                        "elementProperties": {
                            "pageObjectId": slide_id,
                            "size": {"width": {"magnitude": 250, "unit": "PT"}, "height": {"magnitude": 150, "unit": "PT"}},
                            "transform": {"scaleX": 1, "scaleY": 1, "translateX": 430, "translateY": 220, "unit": "PT"},
                        },
                    }
                },
                {
                    "createShape": {
                        "objectId": visual_icon_id,
                        "shapeType": "TEXT_BOX",
                        "elementProperties": {
                            "pageObjectId": slide_id,
                            "size": {"width": {"magnitude": 72, "unit": "PT"}, "height": {"magnitude": 46, "unit": "PT"}},
                            "transform": {"scaleX": 1, "scaleY": 1, "translateX": 606, "translateY": 286, "unit": "PT"},
                        },
                    }
                },
                {
                    "createShape": {
                        "objectId": footer_left_id,
                        "shapeType": "TEXT_BOX",
                        "elementProperties": {
                            "pageObjectId": slide_id,
                            "size": {"width": {"magnitude": 280, "unit": "PT"}, "height": {"magnitude": 20, "unit": "PT"}},
                            "transform": {"scaleX": 1, "scaleY": 1, "translateX": 152, "translateY": 476, "unit": "PT"},
                        },
                    }
                },
                {
                    "createShape": {
                        "objectId": footer_right_id,
                        "shapeType": "TEXT_BOX",
                        "elementProperties": {
                            "pageObjectId": slide_id,
                            "size": {"width": {"magnitude": 160, "unit": "PT"}, "height": {"magnitude": 20, "unit": "PT"}},
                            "transform": {"scaleX": 1, "scaleY": 1, "translateX": 520, "translateY": 476, "unit": "PT"},
                        },
                    }
                },
                {"insertText": {"objectId": section_id, "insertionIndex": 0, "text": page.get("section", "簡報頁")}},
                {"insertText": {"objectId": title_id, "insertionIndex": 0, "text": page.get("title", f"Slide {index + 1}")}},
                {"insertText": {"objectId": key_id, "insertionIndex": 0, "text": page.get("key_message", "")}},
                {"insertText": {"objectId": body_id, "insertionIndex": 0, "text": "分析重點\n" + page.get("supporting_points", page.get("content", ""))}},
                {"insertText": {"objectId": reco_id, "insertionIndex": 0, "text": f"顧問建議\n{page.get('advisor_recommendation', '')}"}},
                {"insertText": {"objectId": visual_icon_id, "insertionIndex": 0, "text": page.get("visual_icon", "📘")}},
                {"insertText": {"objectId": footer_left_id, "insertionIndex": 0, "text": "ET Consulting | Insurance Advisory Proposal"}},
                {"insertText": {"objectId": footer_right_id, "insertionIndex": 0, "text": f"第 {index + 1} 頁"}},
                {
                    "updateTextStyle": {
                        "objectId": section_id,
                        "style": {
                            "foregroundColor": {"opaqueColor": {"rgbColor": {"red": 0.71, "green": 0.33, "blue": 0.04}}},
                            "fontSize": {"magnitude": 10, "unit": "PT"},
                            "bold": True,
                        },
                        "textRange": {"type": "ALL"},
                        "fields": "foregroundColor,fontSize,bold",
                    }
                },
                {
                    "updateTextStyle": {
                        "objectId": title_id,
                        "style": {"bold": True, "fontSize": {"magnitude": 24, "unit": "PT"}},
                        "textRange": {"type": "ALL"},
                        "fields": "bold,fontSize",
                    }
                },
                {
                    "updateTextStyle": {
                        "objectId": key_id,
                        "style": {
                            "fontSize": {"magnitude": 16, "unit": "PT"},
                            "bold": True,
                            "foregroundColor": {"opaqueColor": {"rgbColor": {"red": 0.12, "green": 0.16, "blue": 0.22}}},
                        },
                        "textRange": {"type": "ALL"},
                        "fields": "fontSize,bold,foregroundColor",
                    }
                },
                {
                    "updateTextStyle": {
                        "objectId": body_id,
                        "style": {"fontSize": {"magnitude": 13, "unit": "PT"}},
                        "textRange": {"type": "ALL"},
                        "fields": "fontSize",
                    }
                },
                {
                    "updateTextStyle": {
                        "objectId": reco_id,
                        "style": {"fontSize": {"magnitude": 13, "unit": "PT"}},
                        "textRange": {"type": "ALL"},
                        "fields": "fontSize",
                    }
                },
                {
                    "updateTextStyle": {
                        "objectId": visual_icon_id,
                        "style": {
                            "fontSize": {"magnitude": 32, "unit": "PT"},
                            "bold": True,
                            "foregroundColor": {"opaqueColor": {"rgbColor": {"red": 0.71, "green": 0.33, "blue": 0.04}}},
                        },
                        "textRange": {"type": "ALL"},
                        "fields": "fontSize,bold,foregroundColor",
                    }
                },
                {
                    "updateTextStyle": {
                        "objectId": footer_left_id,
                        "style": {
                            "fontSize": {"magnitude": 9, "unit": "PT"},
                            "foregroundColor": {"opaqueColor": {"rgbColor": {"red": 0.42, "green": 0.45, "blue": 0.5}}},
                        },
                        "textRange": {"type": "ALL"},
                        "fields": "fontSize,foregroundColor",
                    }
                },
                {
                    "updateTextStyle": {
                        "objectId": footer_right_id,
                        "style": {
                            "fontSize": {"magnitude": 9, "unit": "PT"},
                            "foregroundColor": {"opaqueColor": {"rgbColor": {"red": 0.42, "green": 0.45, "blue": 0.5}}},
                            "bold": True,
                        },
                        "textRange": {"type": "ALL"},
                        "fields": "fontSize,foregroundColor,bold",
                    }
                },
            ]
        )
    return requests


def create_google_slides_presentation(title: str, pages: list[dict[str, str]], config: AppConfig) -> tuple[str | None, str | None]:
    credentials = _load_user_credentials(config) or _load_service_account_credentials(config)
    if not credentials:
        return None, None

    try:
        drive_service, slides_service = _build_services(credentials)
        presentation_id, create_meta = _create_presentation_file(title, config, drive_service, slides_service)
        if not presentation_id:
            return None, None

        requests = _build_slide_requests(pages)
        if requests:
            try:
                presentation_doc = slides_service.presentations().get(presentationId=presentation_id).execute()
                first_slide = presentation_doc.get("slides", [{}])[0].get("objectId")
                if first_slide:
                    requests.insert(0, {"deleteObject": {"objectId": first_slide}})
            except Exception:
                pass

            slides_service.presentations().batchUpdate(
                presentationId=presentation_id,
                body={"requests": requests},
            ).execute()

        if config.google_slides_folder_id:
            try:
                current_parents = drive_service.files().get(
                    fileId=presentation_id,
                    fields="parents",
                    supportsAllDrives=create_meta["supports_all_drives"],
                ).execute().get("parents", [])
                drive_service.files().update(
                    fileId=presentation_id,
                    addParents=config.google_slides_folder_id,
                    removeParents=",".join(current_parents) if current_parents else None,
                    fields="id, parents",
                    supportsAllDrives=create_meta["supports_all_drives"],
                ).execute()
            except Exception:
                pass
    except Exception:
        return None, None

    return presentation_id, f"https://docs.google.com/presentation/d/{presentation_id}/edit"


def normalize_google_oauth_client_secret(path_str: str | None) -> str | None:
    if not path_str:
        return None
    path = Path(path_str)
    return str(path) if path.exists() else None


def callback_host(config: AppConfig) -> str:
    return urlparse(config.google_oauth_redirect_uri).netloc
