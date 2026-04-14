# vuln_ruby.rb — intentionally vulnerable Ruby for scanner testing.

# Hardcoded credentials
DB_PASSWORD = "supersecret123"
API_KEY = "sk-hardcoded-api-key-abc123456"

# Command injection via eval
def run_code(user_input)
  eval(user_input)
end

# SQL injection via string interpolation
def get_user(username)
  query = "SELECT * FROM users WHERE name='#{username}'"
  db.execute(query)
end

# Shell injection
def list_files(path)
  system("ls " + path)
end

# Weak hash (MD5)
require 'digest'
def hash_password(password)
  Digest::MD5.hexdigest(password)
end

# Hardcoded secret in URL
def connect_db
  url = "postgres://admin:password123@localhost/mydb"
  url
end
