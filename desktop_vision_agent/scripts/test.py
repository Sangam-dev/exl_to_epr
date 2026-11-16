
import json
import psutil
import time
from datetime import datetime
from pywinauto import Desktop

def get_running_apps():
    """Find visible top-level application windows."""
    desktop = Desktop(backend="uia")
    apps = []

    for window in desktop.windows():
        try:
            title = window.window_text().strip()

            if not title:
                continue

            if not window.is_visible():
                continue

            apps.append({
                "title": title,
                "handle": window.handle,
                "pid": window.process_id(),
                "process": psutil.Process(
                    window.process_id()
                ).name()
            })

        except Exception:
            continue

    # Remove duplicate windows
    unique = {}
    for app in apps:
        unique[app["handle"]] = app

    return list(unique.values())


def extract_element(element):
    """Recursively extract UIA properties."""
    try:
        info = element.element_info

        data = {
            "name": info.name,
            "control_type": info.control_type,
            "automation_id": info.automation_id,
            "class_name": info.class_name,
            "handle": info.handle,
            "rectangle": {
                "left": info.rectangle.left,
                "top": info.rectangle.top,
                "right": info.rectangle.right,
                "bottom": info.rectangle.bottom
            },
            "enabled": element.is_enabled(),
            "visible": element.is_visible(),
            "children": []
        }

        try:
            data["value"] = element.get_value()
        except Exception:
            data["value"] = None

        try:
            data["texts"] = element.texts()
        except Exception:
            data["texts"] = []

        try:
            children = element.children()

            for child in children:
                data["children"].append(
                    extract_element(child)
                )

        except Exception:
            pass

        return data

    except Exception as e:
        return {
            "error": str(e)
        }


def main():
    print("\n========== RUNNING APPLICATIONS ==========\n")

    apps = get_running_apps()

    if not apps:
        print("No visible applications found.")
        return

    for index, app in enumerate(apps):
        print(
            f"[{index + 1}] {app['title']} "
            f"| {app['process']} "
            f"| PID: {app['pid']}"
        )

    print("\n==========================================")

    try:
        choice = int(input("\nChoose application number: ")) - 1

        if choice < 0 or choice >= len(apps):
            print("Invalid selection.")
            return

        selected = apps[choice]

        print(f"\nConnecting to: {selected['title']}")

        desktop = Desktop(backend="uia")

        window = desktop.window(
            handle=selected["handle"]
        )

        print("Extracting UIA hierarchy...")

        ui_data = {
            "application": selected,
            "extraction_time": datetime.now().isoformat(),
            "backend": "uia",
            "window": extract_element(window)
        }

        filename = (
            f"uia_{selected['process'].replace('.exe', '')}_"
            f"{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        )

        with open(filename, "w", encoding="utf-8") as file:
            json.dump(
                ui_data,
                file,
                indent=4,
                ensure_ascii=False
            )

        print(f"\nSUCCESS! UIA data exported.")
        print(f"File: {filename}")

    except ValueError:
        print("Please enter a valid number.")

    except Exception as e:
        print(f"Error: {e}")


if __name__ == "__main__":
    main()
