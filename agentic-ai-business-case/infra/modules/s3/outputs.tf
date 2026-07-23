output "input_bucket_name" {
  value = aws_s3_bucket.input.bucket
}

output "input_bucket_arn" {
  value = aws_s3_bucket.input.arn
}

output "output_bucket_name" {
  value = aws_s3_bucket.output.bucket
}

output "output_bucket_arn" {
  value = aws_s3_bucket.output.arn
}
