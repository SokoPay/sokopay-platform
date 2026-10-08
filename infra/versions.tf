terraform {
  required_version = ">= 1.6"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.60"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }

  # Remote state (recommended). Create the S3 bucket + DynamoDB lock table once,
  # out of band, then uncomment and fill in.
  # backend "s3" {
  #   bucket         = "sokopay-tfstate"
  #   key            = "platform/terraform.tfstate"
  #   region         = "af-south-1"
  #   dynamodb_table = "sokopay-tflock"
  #   encrypt        = true
  # }
}
