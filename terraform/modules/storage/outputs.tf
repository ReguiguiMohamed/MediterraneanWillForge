output "bucket_bronze" {
  description = "Name of the Bronze lakehouse bucket"
  value       = oci_objectstorage_bucket.bronze.name
}

output "bucket_silver" {
  description = "Name of the Silver lakehouse bucket"
  value       = oci_objectstorage_bucket.silver.name
}

output "bucket_gold" {
  description = "Name of the Gold lakehouse bucket"
  value       = oci_objectstorage_bucket.gold.name
}
