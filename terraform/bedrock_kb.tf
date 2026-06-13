locals {
  aoss_collection_name = "${local.name_prefix}-kb"
}

# ─── IAM: Bedrock Knowledge Base Service Role ─────────────────────────────────
# Bedrock assumes this role to read documents from S3 and write vectors to AOSS.

resource "aws_iam_role" "bedrock_kb" {
  name = "${local.name_prefix}-bedrock-kb"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "bedrock.amazonaws.com" }
      Action    = "sts:AssumeRole"
      Condition = {
        StringEquals = { "aws:SourceAccount" = local.account_id }
        ArnLike      = { "aws:SourceArn" = "arn:aws:bedrock:${local.region}:${local.account_id}:knowledge-base/*" }
      }
    }]
  })
}

resource "aws_iam_role_policy" "bedrock_kb_s3" {
  name = "s3-read-knowledge-base"
  role = aws_iam_role.bedrock_kb.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = ["s3:GetObject", "s3:ListBucket"]
      Resource = [
        aws_s3_bucket.knowledge_base.arn,
        "${aws_s3_bucket.knowledge_base.arn}/*",
      ]
    }]
  })
}

resource "aws_iam_role_policy" "bedrock_kb_embedding" {
  name = "bedrock-titan-embed"
  role = aws_iam_role.bedrock_kb.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "bedrock:InvokeModel"
      Resource = "arn:aws:bedrock:${local.region}::foundation-model/amazon.titan-embed-text-v2:0"
    }]
  })
}

resource "aws_iam_role_policy" "bedrock_kb_aoss" {
  name = "aoss-api-access"
  role = aws_iam_role.bedrock_kb.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "aoss:APIAccessAll"
      Resource = aws_opensearchserverless_collection.kb.arn
    }]
  })
}

# ─── OpenSearch Serverless: Security Policies ─────────────────────────────────
# Encryption and network policies must exist before the collection is created.

resource "aws_opensearchserverless_security_policy" "kb_encryption" {
  name = "${local.name_prefix}-kb-enc"
  type = "encryption"

  policy = jsonencode({
    Rules = [{
      Resource     = ["collection/${local.aoss_collection_name}"]
      ResourceType = "collection"
    }]
    AWSOwnedKey = true
  })
}

resource "aws_opensearchserverless_security_policy" "kb_network" {
  name = "${local.name_prefix}-kb-net"
  type = "network"

  policy = jsonencode([{
    Rules = [
      {
        Resource     = ["collection/${local.aoss_collection_name}"]
        ResourceType = "collection"
      },
      {
        Resource     = ["collection/${local.aoss_collection_name}"]
        ResourceType = "dashboard"
      },
    ]
    AllowFromPublic = true
  }])
}

# ─── OpenSearch Serverless: Data Access Policy ────────────────────────────────
# Grants bedrock_kb role (ingestion) and bedrock_scoped role (retrieval) full
# index-level access. Both principals need read; bedrock_kb also needs write.

resource "aws_opensearchserverless_access_policy" "kb" {
  name = "${local.name_prefix}-kb-access"
  type = "data"

  policy = jsonencode([{
    Rules = [
      {
        Resource = ["collection/${local.aoss_collection_name}"]
        Permission = [
          "aoss:CreateCollectionItems",
          "aoss:DeleteCollectionItems",
          "aoss:UpdateCollectionItems",
          "aoss:DescribeCollectionItems",
        ]
        ResourceType = "collection"
      },
      {
        Resource = ["index/${local.aoss_collection_name}/*"]
        Permission = [
          "aoss:CreateIndex",
          "aoss:DeleteIndex",
          "aoss:UpdateIndex",
          "aoss:DescribeIndex",
          "aoss:ReadDocument",
          "aoss:WriteDocument",
        ]
        ResourceType = "index"
      },
    ]
    Principal = [
      aws_iam_role.bedrock_kb.arn,
      aws_iam_role.bedrock_scoped.arn,
    ]
  }])
}

# ─── OpenSearch Serverless: Admin Access Policy ──────────────────────────────
# Grants the deploying IAM identity index-management rights so that
# scripts/create_kb_index.py can create the vector index before the KB is
# provisioned. Separate from the application policy to keep concerns isolated.

resource "aws_opensearchserverless_access_policy" "kb_admin" {
  name = "${local.name_prefix}-kb-admin"
  type = "data"

  policy = jsonencode([{
    Rules = [
      {
        Resource     = ["collection/${local.aoss_collection_name}"]
        Permission   = ["aoss:CreateCollectionItems", "aoss:DescribeCollectionItems"]
        ResourceType = "collection"
      },
      {
        Resource     = ["index/${local.aoss_collection_name}/*"]
        Permission   = ["aoss:CreateIndex", "aoss:DescribeIndex", "aoss:ReadDocument", "aoss:WriteDocument"]
        ResourceType = "index"
      },
    ]
    Principal = [data.aws_caller_identity.current.arn]
  }])
}

# ─── OpenSearch Serverless: Collection ───────────────────────────────────────

resource "aws_opensearchserverless_collection" "kb" {
  name = local.aoss_collection_name
  type = "VECTORSEARCH"

  depends_on = [
    aws_opensearchserverless_security_policy.kb_encryption,
    aws_opensearchserverless_security_policy.kb_network,
  ]
}

# ─── Bedrock Knowledge Base ───────────────────────────────────────────────────
# Uses Titan Embeddings V2 (1024 dims). AOSS stores and retrieves vectors.
# The KB service auto-creates the vector index on first provision if the
# access policy and IAM role are in place before the resource is created.

resource "aws_bedrockagent_knowledge_base" "main" {
  name     = "${local.name_prefix}-kb"
  role_arn = aws_iam_role.bedrock_kb.arn

  knowledge_base_configuration {
    type = "VECTOR"
    vector_knowledge_base_configuration {
      embedding_model_arn = "arn:aws:bedrock:${local.region}::foundation-model/amazon.titan-embed-text-v2:0"
    }
  }

  storage_configuration {
    type = "OPENSEARCH_SERVERLESS"
    opensearch_serverless_configuration {
      collection_arn    = aws_opensearchserverless_collection.kb.arn
      vector_index_name = "bedrock-kb-index"
      field_mapping {
        vector_field   = "bedrock-knowledge-base-default-vector"
        text_field     = "AMAZON_BEDROCK_TEXT_CHUNK"
        metadata_field = "AMAZON_BEDROCK_METADATA"
      }
    }
  }

  depends_on = [
    aws_opensearchserverless_access_policy.kb,
    aws_iam_role_policy.bedrock_kb_aoss,
    aws_iam_role_policy.bedrock_kb_embedding,
    aws_iam_role_policy.bedrock_kb_s3,
  ]
}

# ─── Bedrock Data Source (S3) ─────────────────────────────────────────────────
# FIXED_SIZE chunking: 300 tokens per chunk, 20 % overlap.
# Sidecar metadata files ({doc}.metadata.json) are parsed automatically by
# Bedrock — no extra configuration needed.

resource "aws_bedrockagent_data_source" "kb" {
  knowledge_base_id = aws_bedrockagent_knowledge_base.main.id
  name              = "${local.name_prefix}-s3-docs"

  data_source_configuration {
    type = "S3"
    s3_configuration {
      bucket_arn = aws_s3_bucket.knowledge_base.arn
    }
  }

  vector_ingestion_configuration {
    chunking_configuration {
      chunking_strategy = "FIXED_SIZE"
      fixed_size_chunking_configuration {
        max_tokens         = 300
        overlap_percentage = 20
      }
    }
  }
}
