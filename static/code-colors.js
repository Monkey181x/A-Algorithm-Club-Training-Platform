/* Local, presentation-only code coloring. Never evaluates submitted code. */
(() => {
  'use strict';
  const words={
    cpp:'alignas alignof asm auto break case catch class concept const constexpr consteval constinit continue co_await co_return co_yield decltype default delete do else enum explicit export extern false for friend goto if inline mutable namespace new noexcept nullptr operator private protected public register reinterpret_cast requires return sizeof static static_assert static_cast struct switch template this thread_local throw true try typedef typeid typename union using virtual volatile while',
    c:'auto break case const continue default do else enum extern for goto if inline register restrict return sizeof static struct switch typedef union volatile while',
    python:'and as assert async await break class continue def del elif else except False finally for from global if import in is lambda None nonlocal not or pass raise return True try while with yield match case',
    java:'abstract assert boolean break byte case catch char class const continue default do double else enum extends final finally float for if implements import instanceof int interface long native new null package private protected public return short static strictfp super switch synchronized this throw throws transient true false try void volatile while record sealed permits var',
    javascript:'async await break case catch class const continue debugger default delete do else export extends false finally for from function get if import in instanceof let new null of return set static super switch this throw true try typeof undefined var void while with yield',
    typescript:'abstract any as async await boolean break case catch class const constructor continue declare default delete do else enum export extends false finally for from function get if implements import in infer instanceof interface keyof let module namespace never new null number of private protected public readonly require return set static string super switch symbol this throw true try type typeof undefined unique unknown var void while with yield',
    go:'break case chan const continue default defer else fallthrough for func go goto if import interface map package range return select struct switch type var nil true false',
    rust:'as async await break const continue crate dyn else enum extern false fn for if impl in let loop match mod move mut pub ref return self Self static struct super trait true type unsafe use where while',
    csharp:'abstract as async await base bool break byte case catch char checked class const continue decimal default delegate do double else enum event explicit extern false finally fixed float for foreach goto if implicit in int interface internal is lock long namespace new null object operator out override params private protected public readonly record ref return sbyte sealed short sizeof stackalloc static string struct switch this throw true try typeof uint ulong unchecked unsafe ushort using var virtual void volatile while yield',
    kotlin:'abstract actual annotation as break by catch class companion const constructor continue crossinline data delegate do dynamic else enum expect external false final finally for fun get if import in infix init inline inner interface internal is lateinit noinline null object open operator out override package private protected public reified return sealed set super suspend tailrec this throw true try typealias val var vararg when where while',
    swift:'actor as associatedtype async await break case catch class continue default defer deinit do else enum extension fallthrough false fileprivate for func guard if import in init inout internal is let nil open operator private protocol public repeat rethrows return self static struct subscript super switch throw throws true try typealias var where while',
    sql:'ALL ALTER AND AS ASC BEGIN BETWEEN BY CASE CHECK COLUMN COMMIT CREATE CROSS DATABASE DEFAULT DELETE DESC DISTINCT DROP ELSE END EXISTS FALSE FROM FULL GROUP HAVING IN INDEX INNER INSERT INTO IS JOIN KEY LEFT LIKE LIMIT NOT NULL ON OR ORDER OUTER PRIMARY REFERENCES RIGHT ROLLBACK SELECT SET TABLE THEN TRUE UNION UNIQUE UPDATE VALUES VIEW WHEN WHERE WITH',
    bash:'if then else elif fi case esac for while do done in function select until return export local readonly declare source'
  };
  const types=new Set('bool boolean byte char double float int long short signed size_t ssize_t string unsigned void wchar_t vector map set unordered_map unordered_set array pair tuple list deque queue stack priority_queue optional unique_ptr shared_ptr i32 i64 u32 u64 usize str String Vec HashMap int8 int16 int32 int64 uint uint32 uint64 float32 float64 error'.split(' '));
  const scan=/\/\*[\s\S]*?(?:\*\/|$)|\/\/[^\n]*|#[^\n]*|--[^\n]*|'''[\s\S]*?(?:'''|$)|"""[\s\S]*?(?:"""|$)|"(?:\\[\s\S]|[^"\\])*(?:"|$)|'(?:\\[\s\S]|[^'\\])*(?:'|$)|`(?:\\[\s\S]|[^`\\])*(?:`|$)|\b(?:0[xX][\da-fA-F]+|\d[\w.'-]*(?:\.\d[\w']*)?)\b|\b[A-Za-z_$][\w$]*\b|[{}()[\];,.<>:+*/%=&|!?^~-]/g;
  function colorElement(element,language='text'){
    const source=element.textContent||'';
    if(language==='text'||!words[language])return;
    const keywords=new Set(words[language].split(' '));
    const fragment=document.createDocumentFragment();
    let cursor=0,match,tokens=0;
    scan.lastIndex=0;
    while((match=scan.exec(source))&&tokens++<20000){
      const value=match[0],start=match.index;
      if(start>cursor)fragment.append(document.createTextNode(source.slice(cursor,start)));
      let kind='';
      if(value.startsWith('/*')||value.startsWith('//'))kind='comment';
      else if(value[0]==='#')kind=['python','bash'].includes(language)?'comment':'directive';
      else if(value.startsWith('--')&&language==='sql')kind='comment';
      else if(/^['"`]/.test(value))kind='string';
      else if(/^\d/.test(value))kind='number';
      else if(keywords.has(language==='sql'?value.toUpperCase():value))kind='keyword';
      else if(types.has(value))kind='type';
      else if(/^[A-Za-z_$]/.test(value)&&/^\s*\(/.test(source.slice(scan.lastIndex)))kind='function';
      else if(/^[{}()[\];,.<>:+*/%=&|!?^~-]$/.test(value))kind='operator';
      if(kind){const span=document.createElement('span');span.className='code-token-'+kind;span.style.setProperty('color','var(--code-'+kind+')','important');span.textContent=value;fragment.append(span);}
      else fragment.append(document.createTextNode(value));
      cursor=scan.lastIndex;
    }
    if(cursor<source.length)fragment.append(document.createTextNode(source.slice(cursor)));
    element.replaceChildren(fragment);
  }
  window.CodeColors={colorElement};
})();
