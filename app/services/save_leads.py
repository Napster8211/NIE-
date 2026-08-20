from sqlalchemy.orm import Session
from app.models.lead import Lead
from app.database import SessionLocal
import uuid
from datetime import datetime, timezone

def create_leads():
    leads_data = [
        {
            "business_name": "Mock Restaurants 1",
            "place_id": "ChIJmockplaceid0001",
            "website_domain": "example1.com",
            "business": {"name": "Mock Restaurants 1", "address": "123 Main St, Accra, Ghana"},
            "location": {"city": "Accra", "country": "Ghana", "address": "123 Main St, Accra, Ghana"},
            "contact": {"phone": "+1 555-0100", "email": None},
            "source": {"source": "business_discovery", "url": "https://example1.com"},
            "reputation": {"rating": 4.7, "reviews": 15},
            "qualification": {"status": "qualified", "score": 9, "notes": "Full contact info, website present, high reputation."},
            "metadata_blob": {"source_timestamp": "2025-09-24T00:00:00Z"}
        },
        {
            "business_name": "Mock Restaurants 2",
            "place_id": "ChIJmockplaceid0002",
            "website_domain": "example2.com",
            "business": {"name": "Mock Restaurants 2", "address": "456 Oak St, Accra, Ghana"},
            "location": {"city": "Accra", "country": "Ghana", "address": "456 Oak St, Accra, Ghana"},
            "contact": {"phone": None, "email": None},
            "source": {"source": "business_discovery", "url": "https://example2.com"},
            "reputation": {"rating": 4.2, "reviews": 8},
            "qualification": {"status": "qualified", "score": 7, "notes": "Website present, address known, phone missing."},
            "metadata_blob": {"source_timestamp": "2025-09-24T00:00:00Z"}
        },
        {
            "business_name": "Mock Restaurants 3",
            "place_id": "ChIJmockplaceid0003",
            "website_domain": None,
            "business": {"name": "Mock Restaurants 3", "address": "789 Pine St, Accra, Ghana"},
            "location": {"city": "Accra", "country": "Ghana", "address": "789 Pine St, Accra, Ghana"},
            "contact": {"phone": None, "email": None},
            "source": {"source": "business_discovery", "url": None},
            "reputation": {"rating": 3.5, "reviews": 4},
            "qualification": {"status": "qualified", "score": 5, "notes": "Minimal data, address only."},
            "metadata_blob": {"source_timestamp": "2025-09-24T00:00:00Z"}
        }
    ]

    db = SessionLocal()
    for data in leads_data:
        lead = Lead(
            id=str(uuid.uuid4()),
            business_name=data["business_name"],
            place_id=data["place_id"],
            website_domain=data["website_domain"],
            business=data["business"],
            location=data["location"],
            contact=data["contact"],
            source=data["source"],
            reputation=data["reputation"],
            qualification=data["qualification"],
            metadata_blob=data["metadata_blob"]
        )
        db.add(lead)
    db.commit()
    db.close()

if __name__ == "__main__":
    create_leads()
