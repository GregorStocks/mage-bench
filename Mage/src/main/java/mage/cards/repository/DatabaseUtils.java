package mage.cards.repository;

import com.j256.ormlite.jdbc.JdbcConnectionSource;
import com.j256.ormlite.support.ConnectionSource;
import com.j256.ormlite.support.DatabaseConnection;
import mage.util.DebugUtil;
import org.apache.log4j.Logger;

import java.nio.file.Path;
import java.nio.file.Paths;
import java.sql.SQLException;

/**
 * Helper class for database
 *
 * @author JayDi85
 */
public class DatabaseUtils {

    private static final Logger logger = Logger.getLogger(DatabaseUtils.class);
    private static final String H2_FILE_PREFIX = "jdbc:h2:file:";

    // warning, do not change names or db format
    // h2
    public static final String DB_NAME_FEEDBACK = "feedback.h2";
    public static final String DB_NAME_USERS = "authorized_user.h2";
    public static final String DB_NAME_CARDS = "cards.h2";
    // sqlite (usage reason: h2 database works bad with 1GB+ files and can break it)
    public static final String DB_NAME_RECORDS = "table_record.db";
    public static final String DB_NAME_STATS = "user_stats.db";

    /**
     * Prepare JDBC connection string and setup additional params for H2 databases
     *
     * @param dbName        database name like "cards.h2"
     * @param improveCaches use memory optimizations for cards database (no needs for other dbs)
     */
    public static String prepareH2Connection(String dbName, boolean improveCaches) {
        // example: jdbc:h2:file:./db/cards.h2;AUTO_SERVER=TRUE;IGNORECASE=TRUE
        String res = String.format("jdbc:h2:file:./db/%s", dbName);

        // shared params
        res += ";AUTO_SERVER=TRUE"; // open database in mix mode (first open by new thread, second open by new jvm-process)
        res += ";IGNORECASE=TRUE"; // ignore char case for text searching

        // additional params
        // can be defined by connection string, by exec sql like "SET xxx = yyy", by settings from existing db-file

        if (improveCaches) {
            // CACHE_SIZE
            // max query cache size in kb (default: 65 Mb per 1 GB of java's max memory)
            // warning, xmage require 150Mb cache for big queries in AI games like all card names (db can be broken on lower cache)
            //res += ";CACHE_SIZE=150000";
            res += ";CACHE_SIZE=" + Math.round(Math.max(150000, Runtime.getRuntime().maxMemory() * 0.1 / 1024));


            // QUERY_CACHE_SIZE
            // queries amount per session to cache (default: 8)
            res += ";QUERY_CACHE_SIZE=32";
        }

        // add debug stats (see DebugUtil for usage instruction)
        if (DebugUtil.DATABASE_PROFILE_SQL_QUERIES_TO_FILE) {
            res += ";TRACE_LEVEL_FILE=2";
            res += ";QUERY_STATISTICS=TRUE";
        }

        return res;
    }

    /**
     * Open an H2 database connection, retrying on lock contention.
     *
     * When multiple JVM processes open the same H2 database concurrently
     * (e.g. during golden tests), the file lock can race. AUTO_SERVER=TRUE
     * handles steady-state multi-process access, but the initial lock
     * acquisition can fail if two JVMs try simultaneously. This method
     * retries with backoff so the second JVM waits for the first to finish.
     *
     * @param url JDBC connection URL from {@link #prepareH2Connection}
     */
    public static ConnectionSource openH2ConnectionWithRetry(String url) throws SQLException {
        int maxAttempts = 5;
        int baseDelayMs = 500;
        SQLException lastError = null;
        for (int attempt = 1; attempt <= maxAttempts; attempt++) {
            try {
                JdbcConnectionSource connectionSource = new JdbcConnectionSource(url);
                DatabaseConnection connection = connectionSource.getReadWriteConnection("h2_open_probe");
                connectionSource.releaseConnection(connection);
                return connectionSource;
            } catch (SQLException e) {
                lastError = e;
                if (isUnreadableDatabaseFileError(e)) {
                    throw createUnreadableDatabaseException(url, e);
                }
                if (attempt < maxAttempts) {
                    logger.warn(
                            "H2 connection attempt " + attempt + "/" + maxAttempts
                                    + " failed, retrying in " + (baseDelayMs * attempt) + "ms: " + e.getMessage());
                    try {
                        Thread.sleep(baseDelayMs * attempt);
                    } catch (InterruptedException ie) {
                        Thread.currentThread().interrupt();
                        throw e;
                    }
                }
            }
        }
        throw lastError;
    }

    static boolean isUnreadableDatabaseFileError(SQLException error) {
        Throwable current = error;
        while (current != null) {
            String message = current.getMessage();
            if (message != null && message.contains("Unsupported database file version or invalid file header")) {
                return true;
            }
            if ("org.h2.mvstore.MVStoreException".equals(current.getClass().getName())) {
                return true;
            }
            current = current.getCause();
        }
        return false;
    }

    static IllegalStateException createUnreadableDatabaseException(String url, SQLException cause) {
        Path dbPath = getH2FilePath(url);
        if (dbPath == null) {
            return new IllegalStateException(
                    "Unreadable H2 database for non-file URL " + url
                            + ". Delete or migrate the database manually before restarting.",
                    cause
            );
        }

        Path mvStorePath = dbPath.resolveSibling(dbPath.getFileName() + ".mv.db");
        return new IllegalStateException(
                "Unreadable H2 database file " + mvStorePath
                        + ". Delete or migrate the database manually before restarting.",
                cause
        );
    }

    static Path getH2FilePath(String url) {
        if (!url.startsWith(H2_FILE_PREFIX)) {
            return null;
        }

        int paramsPos = url.indexOf(';');
        String fileName = paramsPos >= 0
                ? url.substring(H2_FILE_PREFIX.length(), paramsPos)
                : url.substring(H2_FILE_PREFIX.length());
        return Paths.get(fileName);
    }

    /**
     * Prepare JDBC connection string and setup additional params for SQLite databases
     *
     * @param dbName database name like "cards"
     */
    public static String prepareSqliteConnection(String dbName) {
        // example: jdbc:sqlite:./db/table_record.db
        return String.format("jdbc:sqlite:./db/%s", dbName);
    }
}
