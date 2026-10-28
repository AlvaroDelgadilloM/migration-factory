package demo.catalog;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

public class CatalogTest {
    @Test
    public void catalogAnswers() {
        assertTrue(Catalog.all("viaje").startsWith("[{\"id\":1,"));
        assertNull(Catalog.one("999"));
        assertEquals("ERROR:quantity fuera de rango (1-50)", Catalog.validate("{\"itemId\":3,\"quantity\":99}"));
        assertTrue(Catalog.stats().contains("\"total\":12"));
    }
}
