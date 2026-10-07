package demo.orders;

import static org.junit.Assert.assertEquals;

import org.junit.Test;

public class OrderTransformerTest {
    @Test
    public void normalizesOrder() {
        assertEquals("ORDER id=A1 amount=10.50", new OrderTransformer().normalize("A1; 10.5"));
    }

    @Test(expected = IllegalArgumentException.class)
    public void rejectsMalformedOrder() {
        new OrderTransformer().normalize("A1");
    }
}
