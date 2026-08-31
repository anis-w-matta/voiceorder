from pydantic import BaseModel


class CustomerDetailOut(BaseModel):
    cust_nb: str
    customer_name: str
    email: str | None = None
    telephone: str | None = None
    city: str | None = None
    address1: str | None = None
    salesman_id: str | None = None


class AssignSalesmanIn(BaseModel):
    salesman_id: str | None = None
