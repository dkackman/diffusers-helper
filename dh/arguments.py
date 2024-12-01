from .toolbox.type_helpers import load_type_from_name
from diffusers.utils import load_image, load_video

#
# This recursvively processes the arguments of a workflow
# replacing type references with the actual types
# loading any images from their locations
#
def realize_args(d):    
    if isinstance(d, dict):
        for k, v in d.items():
            if k.endswith("_image") or k == "image":
                d[k] = fetch_image(v)    
            elif k.endswith("_video") or k == "video":
                d[k] = fetch_video(v)    
            elif isinstance(v, dict): 
                realize_args(v)
            elif isinstance(v, list):
                for item in v:
                    realize_args(item)
            elif (k.endswith("_type") or k.endswith("_dtype")) and k != "content_type":
                # use {} to escape key value pairs that are not type references
                if isinstance(v, str) and v.startswith("{") and v.endswith("}"):
                    d[k] = v.strip("{}")
                else:
                    d[k] = load_type_from_name(v)                  
            
    elif isinstance(d, list):
        for item in d:
            realize_args(item)


def fetch_image(image):
    # escape indicator for intermediate result references
    if isinstance(image, str):
        return image.strip("{}")

    img = load_image(image["location"])
    if "size" in image:
        img = img.resize((image["size"]["height"], image["size"]["width"]))

    return img

def fetch_video(video):
    # escape indicator for intermediate result references
    if isinstance(video, str):
        return video.strip("{}")

    vid = load_video(video["location"])

    return vid