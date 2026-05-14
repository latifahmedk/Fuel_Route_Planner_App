A Django REST API that plans fuel-optimised driving routes between any two locations 
within the USA. Given a start and finish point, it returns the full driving route, the cheapest 
fuel stops along the way based on real OPIS truck stop price data, and the total fuel cost for the trip. 
The vehicle is assumed to have a maximum range of 500 miles and achieves 10 miles per gallon, so multiple 
fuel stops are automatically calculated and displayed. The API uses Nominatim for free geocoding and OSRM for free routing — no paid API keys required.
