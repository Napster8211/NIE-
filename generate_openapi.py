import json
from app.main import app  # Adjust this import based on your actual FastAPI app location

def regenerate_openapi_spec():
    """
    Extracts the auto-generated OpenAPI schema from the FastAPI app
    and writes it to a static JSON file for frontend clients or API gateways.
    """
    # FastAPI natively generates and caches the schema dictionary
    openapi_schema = app.openapi()
    
    # Optional: Inject any custom metadata for the enterprise platform
    openapi_schema["info"]["title"] = "NapsterTec Intelligence Engine"
    openapi_schema["info"]["version"] = "1.4.0"
    openapi_schema["info"]["description"] = "Enterprise multi-modal intelligence and monitoring platform."
    
    output_filename = "openapi.json"
    
    with open(output_filename, "w", encoding="utf-8") as f:
        json.dump(openapi_schema, f, indent=2)
        
    print(f"✅ Successfully regenerated OpenAPI specification: {output_filename}")

if __name__ == "__main__":
    regenerate_openapi_spec()