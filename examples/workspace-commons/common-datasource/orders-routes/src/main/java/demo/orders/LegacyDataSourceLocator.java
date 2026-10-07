package demo.orders;

import javax.naming.InitialContext;
import javax.naming.NamingException;
import javax.sql.DataSource;

/** Acoplamiento JNDI de JBoss; javax.naming y javax.sql son Java SE y no migran a jakarta. */
public class LegacyDataSourceLocator {
    public DataSource lookup() throws NamingException {
        return (DataSource) new InitialContext().lookup("java:jboss/datasources/OrdersDS");
    }
}
