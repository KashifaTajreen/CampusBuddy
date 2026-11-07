import {test,expect} from "@playwright/test";
import {normalizeSpeechText} from "../src/lib/speech-text";

test("English speech preserves time, date and currency values without reading separators",()=>{
  const cases:Record<string,string>={"3:03 PM":"three oh three P M","3:00 PM":"three o'clock P M","09:30 AM":"nine thirty A M","14:45":"two forty five P M","₹150":"one hundred fifty rupees","₹2,500":"two thousand five hundred rupees"};
  for(const [input,expected] of Object.entries(cases))expect(normalizeSpeechText(input,"en")).toBe(expected);
  expect(normalizeSpeechText("02-11-2026","en",{dateFormat:"DMY"})).toBe("two November two thousand twenty six");
  expect(normalizeSpeechText("2026-11-02T14:45:00","en")).toBe("two November two thousand twenty six, two forty five P M");
});
test("Hindi speech uses local time and price units",()=>{
  expect(normalizeSpeechText("3:03 PM, ₹150","hi")).toBe("दोपहर ३ बजकर ३ मिनट, १५० रुपये");
  expect(normalizeSpeechText("02-11-2026","hi",{dateFormat:"DMY"})).toBe("२ नवंबर २०२६");
});
test("Kannada speech uses local time and price units",()=>{
  expect(normalizeSpeechText("14:45, ₹2,500","kn")).toBe("ಮಧ್ಯಾಹ್ನ ೨ ಗಂಟೆ ೪೫ ನಿಮಿಷ, ೨೫೦೦ ರೂಪಾಯಿ");
  expect(normalizeSpeechText("02-11-2026","kn",{dateFormat:"DMY"})).toBe("೨ ನವೆಂಬರ್ ೨೦೨೬");
});
test("ambiguous dates need an explicit format; citation markers and time range punctuation are not spoken",()=>{
  expect(normalizeSpeechText("02-11-2026","en")).toContain("date order unspecified");
  expect(normalizeSpeechText("02-11-2026","en",{dateFormat:"MDY"})).toBe("eleven February two thousand twenty six");
  expect(normalizeSpeechText("Exam 3:03 PM – 4:00 PM [1] [chunk: xyz]","en")).toBe("Exam three oh three P M to four o'clock P M");
  expect(normalizeSpeechText("Dept. CSE USN","en")).toBe("Department C S E U S N");
});
