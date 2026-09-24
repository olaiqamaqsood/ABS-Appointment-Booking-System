# add_column.py
from config import engine
from sqlalchemy import text, inspect

def add_phone_number_column():
    with engine.connect() as conn:
        # Transaction start karte hain (taake agar kuch fail ho toh rollback ho)
        trans = conn.begin()
        try:
            # Check karein ke column already exist toh nahi karta
            inspector = inspect(engine)
            columns = [col["name"] for col in inspector.get_columns("patients")]
            
            if "phone_number" not in columns:
                print("➕ Adding column 'phone_number'...")
                
                # Step 1: Column add karo (NULL allowed)
                conn.execute(text("ALTER TABLE patients ADD COLUMN phone_number VARCHAR"))
                print("   ✅ Column added (nullable).")
                
                # Step 2: Existing rows ke liye unique phone numbers generate karo
                # PostgreSQL mein CONCAT('temp_', id) unique hoga kyunke id unique hai
                conn.execute(text("""
                    UPDATE patients 
                    SET phone_number = 'temp_' || CAST(id AS VARCHAR) 
                    WHERE phone_number IS NULL
                """))
                print("   ✅ Temporary unique phone numbers set for existing rows.")
                
                # Step 3: NOT NULL constraint add karo
                conn.execute(text("ALTER TABLE patients ALTER COLUMN phone_number SET NOT NULL"))
                print("   ✅ NOT NULL constraint added.")
                
                # Step 4: UNIQUE constraint add karo
                conn.execute(text("ALTER TABLE patients ADD CONSTRAINT unique_patient_phone UNIQUE (phone_number)"))
                print("   ✅ UNIQUE constraint added.")
                
                trans.commit()
                print("✅ Column 'phone_number' successfully added with NOT NULL and UNIQUE constraints!")
                
            else:
                print("ℹ️ Column 'phone_number' already exists.")
                
        except Exception as e:
            trans.rollback()
            print(f"❌ Error occurred: {e}")
            print("   Transaction rolled back. No changes were made.")

if __name__ == "__main__":
    add_phone_number_column()