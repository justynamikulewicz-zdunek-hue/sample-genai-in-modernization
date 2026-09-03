data "aws_availability_zones" "available" {
  state = "available"
}

# ---------------------------------------------------------------------------
# VPC
# ---------------------------------------------------------------------------
resource "aws_vpc" "main" {
  cidr_block           = var.vpc_cidr
  enable_dns_hostnames = true
  enable_dns_support   = true

  tags = { Name = "${var.client_name}-vpc" }
}

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id
  tags   = { Name = "${var.client_name}-igw" }
}

# ---------------------------------------------------------------------------
# Subnets — 2 public, 2 private across 2 AZs
# ---------------------------------------------------------------------------
resource "aws_subnet" "public" {
  count                   = 2
  vpc_id                  = aws_vpc.main.id
  cidr_block              = cidrsubnet(var.vpc_cidr, 8, count.index + 1)
  availability_zone       = data.aws_availability_zones.available.names[count.index]
  map_public_ip_on_launch = true

  tags = { Name = "${var.client_name}-public-${count.index + 1}", Tier = "public" }
}

resource "aws_subnet" "private" {
  count             = 2
  vpc_id            = aws_vpc.main.id
  cidr_block        = cidrsubnet(var.vpc_cidr, 8, count.index + 3)
  availability_zone = data.aws_availability_zones.available.names[count.index]

  tags = { Name = "${var.client_name}-private-${count.index + 1}", Tier = "private" }
}

# ---------------------------------------------------------------------------
# No NAT Gateway.
#
# The web tier is a Lambda outside the VPC, and the generation job runs as a
# one-shot Fargate task in a public subnet with a public IP. Nothing needs a
# managed egress path any more, so the NAT Gateway and its Elastic IP are gone
# — measured at ~$23/month, the second largest line on the bill after Fargate.
#
# The private subnets stay (they cost nothing) for workloads that may later
# need to be unreachable from the internet.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Route tables
# ---------------------------------------------------------------------------
resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }

  tags = { Name = "${var.client_name}-public-rt" }
}

# Local routes only — there is no NAT Gateway to point a default route at.
#
# route = [] is stated explicitly rather than omitted: the attribute is
# Optional+Computed, so leaving the block out means "do not manage routes" and
# would strand a blackhole route to the deleted NAT Gateway.
resource "aws_route_table" "private" {
  vpc_id = aws_vpc.main.id
  route  = []

  tags = { Name = "${var.client_name}-private-rt" }
}

resource "aws_route_table_association" "public" {
  count          = 2
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

resource "aws_route_table_association" "private" {
  count          = 2
  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private.id
}

# ---------------------------------------------------------------------------
# Security Groups
# ---------------------------------------------------------------------------
# The ALB security group is gone along with the ALB itself — the app is served
# by a Lambda Function URL, which is not a VPC resource and has no security group.

# Used only by the one-shot Fargate generation task. That task makes outbound
# calls (Bedrock, ECR, S3, DynamoDB) and serves nothing, so it takes no ingress
# at all — a tighter rule than the previous "port 8080 from the ALB".
resource "aws_security_group" "ecs" {
  name        = "${var.client_name}-ecs-sg"
  description = "Fargate generation task - egress only, no inbound"
  vpc_id      = aws_vpc.main.id

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
    description = "All outbound (Bedrock API, ECR, DynamoDB, S3)"
  }

  tags = { Name = "${var.client_name}-ecs-sg" }
}
