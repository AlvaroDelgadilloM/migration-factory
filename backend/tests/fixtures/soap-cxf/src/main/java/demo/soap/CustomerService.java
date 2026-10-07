package demo.soap;

import javax.jws.WebMethod;
import javax.jws.WebService;

@WebService(targetNamespace = "http://demo/customers")
public interface CustomerService {
    @WebMethod
    String findCustomer(String id);
}
