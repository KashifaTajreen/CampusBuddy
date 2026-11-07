/** Speech-only transformations. Never replace the displayed answer with this text. */
export type SpeechDateFormat = "DMY" | "MDY";
type Language = "en" | "hi" | "kn";
const MONTHS = {
  en: "January February March April May June July August September October November December".split(" "),
  hi: "जनवरी फरवरी मार्च अप्रैल मई जून जुलाई अगस्त सितंबर अक्टूबर नवंबर दिसंबर".split(" "),
  kn: "ಜನವರಿ ಫೆಬ್ರವರಿ ಮಾರ್ಚ್ ಏಪ್ರಿಲ್ ಮೇ ಜೂನ್ ಜುಲೈ ಆಗಸ್ಟ್ ಸೆಪ್ಟೆಂಬರ್ ಅಕ್ಟೋಬರ್ ನವೆಂಬರ್ ಡಿಸೆಂಬರ್".split(" "),
};
const SMALL = "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split(" ");
const TENS = "zero ten twenty thirty forty fifty sixty seventy eighty ninety".split(" ");
function englishNumber(n:number):string {
  if(n<20)return SMALL[n];
  if(n<100)return TENS[Math.floor(n/10)]+(n%10 ? " "+SMALL[n%10]:"");
  if(n<1000)return SMALL[Math.floor(n/100)]+" hundred"+(n%100 ? " "+englishNumber(n%100):"");
  if(n<100000)return englishNumber(Math.floor(n/1000))+" thousand"+(n%1000 ? " "+englishNumber(n%1000):"");
  return String(n);
}
export function speechLanguage(text:string,language:"auto"|Language):Language {
  return language!=="auto" ? language : /[\u0c80-\u0cff]/.test(text) ? "kn" : /[\u0900-\u097f]/.test(text) ? "hi" : "en";
}
export function normalizeSpeechText(input:string,requested:"auto"|Language,options:{dateFormat?:SpeechDateFormat}={}):string {
  const lang=speechLanguage(input,requested);
  const num=(n:number)=>lang==="en" ? englishNumber(n) : new Intl.NumberFormat(`${lang}-IN`,{useGrouping:false,numberingSystem:lang==="hi"?"deva":"knda"}).format(n);
  const date=(year:number,month:number,day:number)=>{
    const d=new Date(Date.UTC(year,month-1,day));
    if(d.getUTCFullYear()!==year||d.getUTCMonth()!==month-1||d.getUTCDate()!==day)return null;
    return `${num(day)} ${MONTHS[lang][month-1]} ${num(year)}`;
  };
  let value=input.replace(/\[([^\]]+)\]\(https?:\/\/[^)]+\)/g,"$1")
    .replace(/https?:\/\/\S+/g,"")
    .replace(/\[(?:\d+(?:\s*,\s*\d+)*|(?:source|chunk|citation)[^\]]*)\]/gi,"")
    .replace(/(^|\n)\s*[#>*-]+\s*/g,"$1").replace(/[*`_]/g,"");
  value=value.replace(/(\b(?:[01]?\d|2[0-3]):[0-5]\d(?:\s*[AaPp][Mm])?)\s*[–—-]\s*(?=\d{1,2}:\d{2})/g,`$1 ${lang==="en"?"to":lang==="hi"?"से":"ಇಂದ"} `);
  value=value.replace(/\b(20\d{2})-(\d{2})-(\d{2})(?=T|\b)/g,(raw,y,m,d)=>date(+y,+m,+d)||raw)
    .replace(/(?<=[\p{L}\p{N}])T(?=\d{2}:)/gu,", ");
  value=value.replace(/\b(\d{2})-(\d{2})-(20\d{2})\b/g,(raw,a,b,y)=>{
    if(!options.dateFormat)return `${num(+a)}, ${num(+b)}, ${num(+y)} (${lang==="en"?"numeric date; date order unspecified":lang==="hi"?"तारीख का क्रम स्पष्ट नहीं है":"ದಿನಾಂಕದ ಕ್ರಮ ತಿಳಿದಿಲ್ಲ"})`;
    return date(+y,+(options.dateFormat==="DMY"?b:a),+(options.dateFormat==="DMY"?a:b))||raw;
  });
  value=value.replace(/\b([01]?\d|2[0-3]):([0-5]\d)(?::([0-5]\d))?\s*(AM|PM)?\b/gi,(raw,h,m,s,ampm)=>{
    let hour=+h;
    if(ampm && (hour<1||hour>12))return raw;
    if(ampm)hour=hour%12+(ampm.toUpperCase()==="PM"?12:0);
    const clock=hour%12||12;
    if(lang==="en")return `${num(clock)} ${+m ? (+m<10?"oh ":"")+num(+m):"o'clock"} ${hour<12?"A M":"P M"}${+s?` ${num(+s)} seconds`:""}`;
    const period=lang==="hi" ? (hour<4||hour>=20?"रात":hour<12?"सुबह":hour<17?"दोपहर":"शाम") : (hour<4||hour>=20?"ರಾತ್ರಿ":hour<12?"ಬೆಳಿಗ್ಗೆ":hour<17?"ಮಧ್ಯಾಹ್ನ":"ಸಂಜೆ");
    return lang==="hi" ? `${period} ${num(clock)} ${+m?`बजकर ${num(+m)} मिनट`:"बजे"}${+s?` ${num(+s)} सेकंड`:""}` : `${period} ${num(clock)} ಗಂಟೆ${+m?` ${num(+m)} ನಿಮಿಷ`:""}${+s?` ${num(+s)} ಸೆಕೆಂಡ್`:""}`;
  });
  value=value.replace(/₹\s*(\d[\d,]*(?:\.\d{1,2})?)/g,(_raw,amount)=>{
    const [whole,fraction=""] = amount.replaceAll(",","").split(".");
    const paise=Number(fraction.padEnd(2,"0"));
    return `${num(+whole)} ${lang==="en"?"rupees":lang==="hi"?"रुपये":"ರೂಪಾಯಿ"}${paise?` ${num(paise)} ${lang==="en"?"paise":lang==="hi"?"पैसे":"ಪೈಸೆ"}`:""}`;
  });
  const words:Record<string,string>={USN:"U S N",CSE:"C S E",AIML:"A I M L",AI:"A I",ML:"M L",DBMS:"D B M S",OS:"Operating Systems",AM:"A M",PM:"P M"};
  value=value.replace(/\b(USN|CSE|AIML|AI|ML|DBMS|OS|AM|PM)\b/g,(word)=>words[word]);
  value=value.replace(/\b(?:Dr|Prof|Dept|Sem)\./g,(word)=>({"Dr.":"Doctor","Prof.":"Professor","Dept.":lang==="hi"?"विभाग":lang==="kn"?"ವಿಭಾಗ":"Department","Sem.":lang==="hi"?"सेमेस्टर":lang==="kn"?"ಸೆಮಿಸ್ಟರ್":"Semester"}[word]||word));
  return value.replace(/[–—]/g,", ").replace(/\s+/g," ").trim();
}
