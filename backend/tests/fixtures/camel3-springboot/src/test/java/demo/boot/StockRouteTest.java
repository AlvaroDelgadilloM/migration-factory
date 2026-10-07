package demo.boot;

import org.junit.jupiter.api.Test;

class StockRouteTest {
    @Test
    void routeClassLoads() {
        new StockRoute();
    }
}
