package org.mage.test.serverside;

import mage.cards.repository.DatabaseUtils;
import mage.server.AuthorizedUser;
import mage.server.AuthorizedUserRepository;
import org.junit.Assert;
import org.junit.Ignore;
import org.junit.Rule;
import org.junit.Test;
import org.junit.rules.TemporaryFolder;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Paths;

/**
 * Testing database compatible on new libs or updates.
 *
 * @author JayDi85
 */
public class DatabaseCompatibleTest {

    @Rule
    public TemporaryFolder tempFolder = new TemporaryFolder();

    @Test
    public void test_AuthUsers() {
        try {
            String dbDir = tempFolder.newFolder().getAbsolutePath();
            String dbName = "users-db-sample.h2";
            String dbFullName = Paths.get(dbDir, dbName).toAbsolutePath().toString();
            String connectionString = String.format("jdbc:h2:file:%s;AUTO_SERVER=TRUE", dbFullName);
            AuthorizedUserRepository dbUsers = new AuthorizedUserRepository(connectionString);
            dbUsers.add("user1", "pas1", "user1@example.com");
            dbUsers.add("user2", "pas2", "user2@example.com");
            dbUsers.add("user3", "pas3", "user3@example.com");

            // search
            Assert.assertNotNull(dbUsers.getByName("user1"));
            Assert.assertNotNull(dbUsers.getByEmail("user2@example.com"));
            Assert.assertNull(dbUsers.getByName("userFAIL"));

            // login
            AuthorizedUser user = dbUsers.getByName("user3");
            Assert.assertEquals("user name", user.getName(), "user3");
            Assert.assertTrue("user pas", user.doCredentialsMatch("user3", "pas3"));
            Assert.assertFalse("user wrong pas", user.doCredentialsMatch("user3", "123"));
            Assert.assertFalse("user empty pas", user.doCredentialsMatch("user3", ""));
            dbUsers.closeDB();

            AuthorizedUserRepository reopenedDbUsers = new AuthorizedUserRepository(connectionString);
            Assert.assertNotNull(reopenedDbUsers.getByName("user1"));
            reopenedDbUsers.closeDB();
        } catch (IOException e) {
            e.printStackTrace();
            Assert.fail(e.getMessage());
        }
    }

    @Test
    public void test_AuthUsersUnreadableDbFileFailsFast() {
        try {
            String dbDir = tempFolder.newFolder().getAbsolutePath();
            String dbName = "users-db-sample.h2";
            String dbFullName = Paths.get(dbDir, dbName).toAbsolutePath().toString();
            String dbFullFileName = dbFullName + ".mv.db";
            Files.writeString(Paths.get(dbFullFileName), "legacy h2");
            Assert.assertTrue(Files.exists(Paths.get(dbFullFileName)));

            String connectionString = String.format("jdbc:h2:file:%s;AUTO_SERVER=TRUE", dbFullName);
            IllegalStateException error = Assert.assertThrows(
                    IllegalStateException.class,
                    () -> DatabaseUtils.openH2ConnectionWithRetry(connectionString)
            );
            Assert.assertTrue(error.getMessage().contains(dbFullFileName));
            Assert.assertTrue(error.getMessage().contains("Delete or migrate"));
        } catch (IOException e) {
            e.printStackTrace();
            Assert.fail(e.getMessage());
        }
    }

    @Test
    @Ignore // TODO: add records/stats db compatible test
    public void test_Records() {
    }
}
