package demo.shop;

import static org.junit.Assert.assertEquals;

import java.util.HashMap;
import java.util.Map;

import org.junit.Test;

public class OrderValidatorTest {

    private Map<String, Object> order(Object customer, Object product, Object qty) {
        Map<String, Object> m = new HashMap<>();
        m.put("customerId", customer);
        m.put("productId", product);
        m.put("quantity", qty);
        return m;
    }

    @Test
    public void acceptsAValidOrder() {
        assertEquals(3, OrderValidator.positive(order(10, 20, 3), "quantity", 100));
    }

    @Test(expected = IllegalArgumentException.class)
    public void rejectsQuantityOutOfRange() {
        OrderValidator.positive(order(10, 20, 500), "quantity", 100);
    }

    @Test(expected = IllegalArgumentException.class)
    public void rejectsMissingField() {
        OrderValidator.positive(order(10, null, 1), "productId", 300);
    }
}
