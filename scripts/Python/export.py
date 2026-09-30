#TOPIC export_firestore.py
from __future__ import annotations


#TOPIC Importing libraries
import os
import re
import json
import base64
import decimal
from pathlib import Path
from datetime import datetime

from dotenv import load_dotenv
from google.oauth2 import service_account
from google.cloud import firestore
from google.api_core.datetime_helpers import DatetimeWithNanoseconds
from google.cloud.firestore_v1 import DocumentReference, GeoPoint # type: ignore
from google.cloud.firestore_v1.services.firestore import FirestoreClient
from google.cloud.firestore_v1.services.firestore.transports import FirestoreRestTransport



#TOPIC Config inicial 
# Silencia logs do gRPC/absl (caso use transporte gRPC)
os.environ.setdefault("GRPC_VERBOSITY", "ERROR")
# Carrega .env
load_dotenv(override=True)


#TOPIC Credenciais & Client 
def build_credentials_from_env():
    """Monta credenciais de service account a partir do .env e valida mínimos."""
    info = {
        "type": os.getenv("TYPE", "service_account"),
        "project_id": os.getenv("PROJECT_ID"),
        "private_key_id": os.getenv("PRIVATE_KEY_ID"),
        "private_key": (os.getenv("PRIVATE_KEY") or "").replace("\\n", "\n"),
        "client_email": os.getenv("CLIENT_EMAIL"),
        "client_id": os.getenv("CLIENT_ID"),
        "auth_uri": os.getenv("AUTH_URI") or "https://accounts.google.com/o/oauth2/auth",
        "token_uri": (os.getenv("TOKEN_URI") or "https://oauth2.googleapis.com/token").strip(),
        "auth_provider_x509_cert_url": os.getenv("AUTH_PROVIDER_X509_CERT_URL")
        or "https://www.googleapis.com/oauth2/v1/certs",
        "client_x509_cert_url": os.getenv("CLIENT_X509_CERT_URL"),
        "universe_domain": os.getenv("UNIVERSE_DOMAIN") or "googleapis.com",
    }

    # Corrige vírgula/whitespace acidental em TOKEN_URI (causa 404 /token,)
    info["token_uri"] = re.sub(r"[,\s]+$", "", info["token_uri"])

    for k in ("project_id", "private_key", "client_email", "token_uri"):
        if not info.get(k):
            raise ValueError(f"Missing {k} in service account info (.env)")

    # Escopos explícitos
    scopes = [
        "https://www.googleapis.com/auth/cloud-platform",
        "https://www.googleapis.com/auth/datastore",
    ]
    creds = service_account.Credentials.from_service_account_info(info, scopes=scopes)
    return creds, info["project_id"]


def get_firestore_client():
    """Inicializa o cliente do Firestore usando REST por padrão."""
    creds, project_id = build_credentials_from_env()

    transport = (os.getenv("FIRESTORE_TRANSPORT") or "rest").strip().lower()
    if transport not in {"rest", "grpc"}:
        raise ValueError("FIRESTORE_TRANSPORT deve ser 'rest' ou 'grpc'.")

    if transport == "rest":
        client = firestore.Client(project=project_id, credentials=creds)
        client._firestore_api_internal = FirestoreClient(transport=FirestoreRestTransport(credentials=creds))
        return client

    return firestore.Client(project=project_id, credentials=creds)


#TOPIC Normalização para JSON
def _norm(value):
    # Simples
    if value is None or isinstance(value, (int, float, str, bool)):
        return value
    # datetime (inclui DatetimeWithNanoseconds)
    if isinstance(value, (datetime, DatetimeWithNanoseconds)):
        return value.isoformat()
    # Referência de documento
    if isinstance(value, DocumentReference):
        return value.path  # ex.: "usuarios/abc123/pedidos/p001"
    # GeoPoint
    if isinstance(value, GeoPoint):
        return {"latitude": value.latitude, "longitude": value.longitude}
    # bytes
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {"__type__": "bytes", "base64": base64.b64encode(bytes(value)).decode("ascii")}
    # Decimal
    if isinstance(value, decimal.Decimal):
        return float(value)
    # Containers
    if isinstance(value, (set, tuple, list)):
        return [_norm(v) for v in value]
    if isinstance(value, dict):
        return {k: _norm(v) for k, v in value.items()}
    # Fallback
    return str(value)


def normalize_for_json(obj):
    return _norm(obj)

#TOPIC Export recursivo
def export_collection_recursive(
    db: firestore.Client, collection_path: str, include_subcollections: bool = True
) -> dict:
    """
    Exporta todos os documentos de `collection_path` e, para cada documento,
    todas as subcoleções (recursivamente).
    """
    out: dict[str, dict] = {}
    col_ref = db.collection(collection_path)

    for doc_snap in col_ref.stream():
        doc_data = doc_snap.to_dict() or {}
        sub_out: dict[str, dict] = {}

        if include_subcollections:
            for subcol_ref in doc_snap.reference.collections():
                sub_path = f"{collection_path}/{doc_snap.id}/{subcol_ref.id}"
                sub_out[subcol_ref.id] = export_collection_recursive(db, sub_path, include_subcollections=True)

        if sub_out:
            doc_data["__collections__"] = sub_out

        out[doc_snap.id] = doc_data

    return out


#TOPIC Utilidades de caminho
def resolve_out_path(out_file: str) -> Path:
    """
    Se o OUT_FILE for relativo, resolve-o a partir da **raiz do projeto**
    (2 níveis acima deste arquivo: .../scripts/Python -> raiz).
    Garante que o diretório exista.
    """
    script_dir = Path(__file__).resolve().parent          # .../scripts/Python
    project_root = script_dir.parents[1]                  # raiz do projeto

    out_path = Path(out_file).expanduser()
    if not out_path.is_absolute():
        out_path = (project_root / out_path).resolve()

    out_path.parent.mkdir(parents=True, exist_ok=True)
    return out_path


#TOPIC Main
def main():
    # Leitura de parâmetros via env
    root = os.getenv("ROOT_COLLECTION", "usuarios").strip().strip("/")
    if "/" in root:
        raise ValueError("ROOT_COLLECTION deve ser apenas o NOME da coleção raiz (ex.: 'usuarios').")

    # Caminho de saída com data de exportação
    out_file = os.getenv("OUT_FILE", f"{root}-backup-{datetime.now().strftime('%Y%m%d')}.json")

    # Firestore client
    db = get_firestore_client()

    include_subcollections = os.getenv("EXPORT_SUBCOLLECTIONS", "true").strip().lower() in {
        "1",
        "true",
        "yes",
        "sim",
    }

    # Exporta e normaliza
    data = {root: export_collection_recursive(db, root, include_subcollections=include_subcollections)}
    data = normalize_for_json(data)

    # Caminho de saída (cria pastas se necessário)
    out_path = resolve_out_path(out_file)

    # Grava JSON
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"✅ Export concluído! Arquivo salvo em:\n{out_path}")


if __name__ == "__main__":
    main()