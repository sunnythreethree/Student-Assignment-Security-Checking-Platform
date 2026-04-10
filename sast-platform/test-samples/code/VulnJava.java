/**
 * VulnJava.java — intentionally vulnerable Java for scanner testing.
 * Expected: findings from Semgrep (SQL injection, hardcoded credentials).
 */
import java.sql.*;

public class VulnJava {

    // Hardcoded credentials
    private static final String DB_PASSWORD = "admin123";
    private static final String DB_URL = "jdbc:mysql://localhost/mydb";

    // SQL injection via string concatenation
    public static ResultSet getUser(String username) throws SQLException {
        Connection conn = DriverManager.getConnection(DB_URL, "root", DB_PASSWORD);
        Statement stmt = conn.createStatement();
        String query = "SELECT * FROM users WHERE name = '" + username + "'";
        return stmt.executeQuery(query);
    }

    // Command injection via Runtime.exec with user input
    public static void runCommand(String userInput) throws Exception {
        Runtime.getRuntime().exec("ls " + userInput);
    }

    // Hardcoded secret key
    public static String getApiKey() {
        return "hardcoded-api-key-9876";
    }
}
