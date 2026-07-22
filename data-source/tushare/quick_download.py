import sys
import time
from downloader import TushareDownloader
from api_registry import get_apis_by_category

print("Quick Download (No Testing)")
print("="*60)

downloader = TushareDownloader()

categories = ["fx", "spot", "options", "news"]
for category in categories:
    print(f"\nProcessing {category}...")
    apis = [api for api in get_apis_by_category(category) if api.enabled]
    
    for api in apis:
        print(f"  - {api.api_name}")
        try:
            stats = downloader.download_all(api_names=[api.api_name])
            print(f"    Result: {stats['completed']} completed, {stats['skipped']} skipped, {stats['failed']} failed")
        except Exception as e:
            print(f"    Error: {e}")
            
print("\nDone!")
