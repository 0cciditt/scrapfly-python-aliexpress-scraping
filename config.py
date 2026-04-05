import os
import re

from dotenv import load_dotenv
from scrapfly import ScrapflyClient

load_dotenv()


def normalize_url(url: str) -> str:
    """Convert localized AliExpress URLs (e.g. es.aliexpress.com) to www.aliexpress.com."""
    return re.sub(r"https?://\w+\.aliexpress\.(?:com|us)", "https://www.aliexpress.com", url)


BASE_CONFIG = {
    "asp": True,
    "country": "GB",
    "headers": {
        "cookie": "aep_usuc_f=site=glo&province=&city=&c_tp=USD&region=US&b_locale=en_US&ae_u_p_s=2"
    },
}

SCRAPFLY = ScrapflyClient(key=os.environ["SCRAPFLY_KEY"])
