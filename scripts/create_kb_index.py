"""Create the AOSS vector index required by Bedrock Knowledge Base.

Bedrock KB does not auto-create the index in AOSS — it must exist before
`aws_bedrockagent_knowledge_base` is provisioned.

Run once after the AOSS collection is ACTIVE (after first `terraform apply`),
then run `terraform apply` again to create the KB.

Usage:
    uv run python scripts/create_kb_index.py
"""

import sys

import boto3
from opensearchpy import AWSV4SignerAuth, OpenSearch, RequestsHttpConnection

REGION          = "eu-central-1"
COLLECTION_NAME = "iam-gateway-dev-kb"
INDEX_NAME      = "bedrock-kb-index"

# Field names must match bedrock_kb.tf field_mapping exactly.
# dimension=1024 matches Titan Embeddings V2 default output size.
INDEX_MAPPING = {
    "settings": {
        "index": {
            "knn": True,
            "knn.algo_param.ef_search": 512,
        }
    },
    "mappings": {
        "properties": {
            "bedrock-knowledge-base-default-vector": {
                "type": "knn_vector",
                "dimension": 1024,
                "method": {
                    "name": "hnsw",
                    "engine": "faiss",
                    "space_type": "l2",
                    "parameters": {
                        "ef_construction": 512,
                        "m": 16,
                    },
                },
            },
            "AMAZON_BEDROCK_TEXT_CHUNK": {
                "type": "text",
            },
            "AMAZON_BEDROCK_METADATA": {
                "type": "text",
                "index": False,
            },
        }
    },
}


def main() -> None:
    session     = boto3.Session(region_name=REGION)
    credentials = session.get_credentials()
    auth        = AWSV4SignerAuth(credentials, REGION, "aoss")

    # Resolve collection endpoint
    aoss    = session.client("opensearchserverless", region_name=REGION)
    resp    = aoss.batch_get_collection(names=[COLLECTION_NAME])
    details = resp.get("collectionDetails", [])
    if not details:
        print(f"ERROR: collection '{COLLECTION_NAME}' not found.")
        sys.exit(1)

    status   = details[0]["status"]
    endpoint = details[0]["collectionEndpoint"]  # https://xxx.aoss.amazonaws.com
    print(f"Collection: {endpoint}  status={status}")

    if status != "ACTIVE":
        print(f"ERROR: collection must be ACTIVE, current status: {status}")
        sys.exit(1)

    host   = endpoint.replace("https://", "")
    client = OpenSearch(
        hosts=[{"host": host, "port": 443}],
        http_auth=auth,
        use_ssl=True,
        verify_certs=True,
        connection_class=RequestsHttpConnection,
        pool_maxsize=20,
    )

    if client.indices.exists(index=INDEX_NAME):
        print(f"Index '{INDEX_NAME}' already exists — nothing to do.")
        return

    response = client.indices.create(index=INDEX_NAME, body=INDEX_MAPPING)
    print(f"Index created: {response}")


if __name__ == "__main__":
    main()
