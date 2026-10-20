variable "name" {
  description = "Globally unique bucket name."
  type        = string
}

variable "force_destroy" {
  description = "Allow destroying the bucket with objects in it (dev only)."
  type        = bool
  default     = false
}

variable "noncurrent_version_days" {
  type    = number
  default = 30
}

variable "session_archive_days" {
  type    = number
  default = 30
}

variable "tags" {
  type    = map(string)
  default = {}
}
