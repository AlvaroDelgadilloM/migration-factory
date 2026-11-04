package demo.pedidos;

/** Errores de negocio del servicio de pedidos. Cada uno se traduce a un código HTTP en PedidosRoutes. */
public final class Errores {

    private Errores() {
    }

    /** 400: la petición no trae lo necesario o trae valores fuera de rango. */
    public static class Validacion extends RuntimeException {
        public Validacion(String mensaje) {
            super(mensaje);
        }
    }

    /** 404: el cliente, producto o pedido no existe. */
    public static class NoEncontrado extends RuntimeException {
        public NoEncontrado(String mensaje) {
            super(mensaje);
        }
    }

    /** 409: no hay existencias o el pedido no admite la operación en su estado. */
    public static class Conflicto extends RuntimeException {
        public Conflicto(String mensaje) {
            super(mensaje);
        }
    }

    /** 422: el cliente existe pero no puede comprar. */
    public static class ClienteInactivo extends RuntimeException {
        public ClienteInactivo(String mensaje) {
            super(mensaje);
        }
    }

    /** 422: el buró de crédito respondió que no. */
    public static class CreditoRechazado extends RuntimeException {
        public CreditoRechazado(String mensaje) {
            super(mensaje);
        }
    }
}
