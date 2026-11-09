/* Ambient EEG field behind the empty workspace: particles drift along slow sine "channels",
 * link to near neighbours and lean away from the pointer. Runs only while #empty is shown. */
const AmbientField=(()=>{
  function mount(host,chat){
    const canvas=document.createElement('canvas');canvas.className='ambient';canvas.setAttribute('aria-hidden','true');host.prepend(canvas);
    const ctx=canvas.getContext('2d'),still=matchMedia('(prefers-reduced-motion: reduce)');
    let width=0,height=0,ratio=1,frame=0,running=false,particles=[],ink=[20,20,19],accent=[217,119,87];
    const pointer={x:-1e4,y:-1e4,active:false};
    const CHANNELS=6,LINK=96;

    function rgb(name,fallback){
      const probe=document.createElement('span');probe.style.color=getComputedStyle(document.documentElement).getPropertyValue(name).trim();
      document.body.appendChild(probe);const match=getComputedStyle(probe).color.match(/\d+(\.\d+)?/g);probe.remove();
      return match?match.slice(0,3).map(Number):fallback;
    }
    function palette(){ink=rgb('--md-on-surface',ink);accent=rgb('--accent',accent)}
    const target=()=>Math.round(Math.min(140,Math.max(48,width*height/10000)));
    function seed(){
      particles=Array.from({length:target()},(_,index)=>({
        channel:index%CHANNELS,x:Math.random()*width,jitter:(Math.random()-.5)*18,
        speed:.12+Math.random()*.32,size:.7+Math.random()*1.3,twinkle:Math.random()*Math.PI*2,
        warm:Math.random()<.32,dx:0,dy:0
      }));
    }
    function resize(){
      const rect=host.getBoundingClientRect();ratio=Math.min(2,devicePixelRatio||1);
      width=rect.width;height=rect.height;canvas.width=Math.round(width*ratio);canvas.height=Math.round(height*ratio);
      ctx.setTransform(ratio,0,0,ratio,0,0);if(Math.abs(particles.length-target())>24)seed();
      if(!running&&visible())draw(performance.now());
    }
    function wave(channel,x,time){
      const band=height*.16+channel*(height*.62/(CHANNELS-1)),amp=10+channel%3*7,freq=.0042+channel*.0007;
      return band+Math.sin(x*freq+time*.00035*(1+channel*.18)+channel*1.7)*amp+Math.sin(x*freq*2.7+time*.0009+channel)*amp*.28;
    }
    function draw(time){
      ctx.clearRect(0,0,width,height);
      for(let channel=0;channel<CHANNELS;channel++){
        const color=channel===2?accent:ink;ctx.strokeStyle=`rgba(${color[0]},${color[1]},${color[2]},${channel===2?.16:.06})`;
        ctx.lineWidth=1;ctx.beginPath();
        for(let x=-8;x<=width+8;x+=8){const y=wave(channel,x,time);x<0?ctx.moveTo(x,y):ctx.lineTo(x,y)}
        ctx.stroke();
      }
      for(const p of particles){
        if(!still.matches)p.x+=p.speed;if(p.x>width+12)p.x=-12;
        let y=wave(p.channel,p.x,time)+p.jitter,ox=0,oy=0;
        if(pointer.active){const vx=p.x-pointer.x,vy=y-pointer.y,d=Math.hypot(vx,vy);if(d<140&&d>.1){const push=(1-d/140)**2*26;ox=vx/d*push;oy=vy/d*push}}
        p.dx+=(ox-p.dx)*.08;p.dy+=(oy-p.dy)*.08;p.px=p.x+p.dx;p.py=y+p.dy;
        p.alpha=.22+.26*(.5+.5*Math.sin(time*.0012+p.twinkle));
      }
      for(let i=0;i<particles.length;i++){
        const a=particles[i];
        for(let j=i+1;j<particles.length;j++){
          const b=particles[j],d=Math.hypot(a.px-b.px,a.py-b.py);if(d>LINK)continue;
          const color=a.warm&&b.warm?accent:ink;ctx.strokeStyle=`rgba(${color[0]},${color[1]},${color[2]},${(1-d/LINK)*.11})`;
          ctx.lineWidth=.6;ctx.beginPath();ctx.moveTo(a.px,a.py);ctx.lineTo(b.px,b.py);ctx.stroke();
        }
      }
      for(const p of particles){
        const color=p.warm?accent:ink,near=pointer.active?Math.max(0,1-Math.hypot(p.px-pointer.x,p.py-pointer.y)/160):0;
        ctx.fillStyle=`rgba(${color[0]},${color[1]},${color[2]},${Math.min(.85,(p.warm?p.alpha*1.5:p.alpha)+near*.4)})`;
        ctx.beginPath();ctx.arc(p.px,p.py,p.size+near*.8,0,Math.PI*2);ctx.fill();
      }
    }
    function loop(time){if(!running)return;draw(time);frame=requestAnimationFrame(loop)}
    function visible(){return Boolean(chat.querySelector('#empty'))&&document.visibilityState==='visible'}
    function sync(){
      const show=visible();canvas.classList.toggle('on',show);
      if(show&&!running){if(!width)resize();if(still.matches){draw(performance.now());return}running=true;frame=requestAnimationFrame(loop)}
      else if(!show&&running){running=false;cancelAnimationFrame(frame)}
    }
    host.addEventListener('pointermove',event=>{const rect=host.getBoundingClientRect();pointer.x=event.clientX-rect.left;pointer.y=event.clientY-rect.top;pointer.active=true},{passive:true});
    host.addEventListener('pointerleave',()=>{pointer.active=false});
    new ResizeObserver(resize).observe(host);
    new MutationObserver(sync).observe(chat,{childList:true});
    new MutationObserver(()=>{palette();if(!running&&visible())draw(performance.now())}).observe(document.documentElement,{attributes:true,attributeFilter:['data-theme']});
    document.addEventListener('visibilitychange',sync);still.addEventListener('change',()=>{running=false;cancelAnimationFrame(frame);sync()});
    palette();resize();sync();
  }
  return {mount};
})();
