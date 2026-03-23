import os
import sys
import time
import argparse
import google.generativeai as genai
from dotenv import load_dotenv

def main():
    load_dotenv()
    parser = argparse.ArgumentParser(description="Debug Gemini Prompt")
    parser.add_argument("--audio", default="data/raw/rtHjR6YFr00.wav", help="Local audio file to analyze")
    args = parser.parse_args()

    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        print("Please set GOOGLE_API_KEY environment variable.")
        sys.exit(1)
        
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel("models/gemini-2.5-pro")
    
    prompt = "請聆聽這段音檔，總結分析師對哪些台股標的看多並引述他的論點"
    
    print(f"Uploading audio file: {args.audio}")
    try:
        audio_file = genai.upload_file(path=args.audio)
        print(f"File uploaded... checking state... URI: {audio_file.uri}")
        
        while audio_file.state.name == "PROCESSING":
            print(".", end="", flush=True)
            time.sleep(5)
            audio_file = genai.get_file(audio_file.name)
            
        print()
        if audio_file.state.name == "FAILED":
            print("File processing failed.")
            sys.exit(1)
            
    except Exception as e:
        print(f"Error uploading/processing file: {e}")
        sys.exit(1)

    print(f"Prompt: {prompt}")
    print("Sending request to Gemini with uploaded audio...")
    
    try:
        response = model.generate_content([audio_file, prompt])
        print("\n--- Gemini Response ---\n")
        print(response.text)
    except Exception as e:
        print(f"Error: {e}")
    finally:
        print("\nCleaning up uploaded file...")
        genai.delete_file(audio_file.name)

if __name__ == "__main__":
    main()
