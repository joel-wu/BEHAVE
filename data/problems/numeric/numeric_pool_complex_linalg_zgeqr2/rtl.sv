module complex_qr2x2_householder (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [15:0]  in_a00_re,
    input  logic [15:0]  in_a00_im,
    input  logic [15:0]  in_a01_re,
    input  logic [15:0]  in_a01_im,
    input  logic [15:0]  in_a10_re,
    input  logic [15:0]  in_a10_im,
    input  logic [15:0]  in_a11_re,
    input  logic [15:0]  in_a11_im,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [15:0]  out_r00_re,
    output logic [15:0]  out_r00_im,
    output logic [15:0]  out_r01_re,
    output logic [15:0]  out_r01_im,
    output logic [15:0]  out_r10_re,
    output logic [15:0]  out_r10_im,
    output logic [15:0]  out_r11_re,
    output logic [15:0]  out_r11_im,
    output logic [15:0]  out_tau0_re,
    output logic [15:0]  out_tau0_im,
    output logic [15:0]  out_tau1_re,
    output logic [15:0]  out_tau1_im,
    output logic [15:0]  out_v10_re,
    output logic [15:0]  out_v10_im
);

function automatic logic signed [63:0] rn(input logic signed [127:0] n,d);
    logic signed [127:0] a,b,q;
    a=(n<0)?-n:n;b=(d<0)?-d:d;
    q=(b==0)?0:(a+b/2)/b;rn=((n<0)!=(d<0))?-q:q;
endfunction
function automatic logic signed [63:0] rt(input logic [127:0] n);
    logic [127:0] r,t;r=0;
    for(integer b=31;b>=0;b=b-1) begin t=r|(128'd1<<b);if(t*t<=n)r=t;end
    rt=r[63:0];
endfunction
function automatic logic [15:0] enc(input logic signed [63:0] v,d);
    logic signed [63:0] x;x=rn(v,d);
    enc=x>32767?16'h7fff:x< -32768?16'h8000:x[15:0];
endfunction
task automatic cp(input logic signed [63:0] ar,ai,br,bi,
                  output logic signed [63:0] r,i);
    logic signed [127:0] a,b,c,d;a=ar;b=ai;c=br;d=bi;
    r=rn(a*c,16384)-rn(b*d,16384);i=rn(a*d,16384)+rn(b*c,16384);
endtask
task automatic refl(input logic signed [63:0] ar,ai,xr,xi,
                    output logic signed [63:0] beta,tr,ti,vr,vi);
    logic signed [127:0] a,b,x,y,d,n;a=ar;b=ai;x=xr;y=xi;
    beta=ar;tr=0;ti=0;vr=0;vi=0;
    if(xr!=0 || xi!=0 || ai!=0) begin
        n=rt(a*a+b*b+x*x+y*y);beta=(ar>=0)?-n:n;
        n=beta;tr=rn((n-a)*16384,n);ti=rn(-b*16384,n);
        d=(a-n)*(a-n)+b*b;
        vr=rn((x*(a-n)+y*b)*16384,d);vi=rn((y*(a-n)-x*b)*16384,d);
    end
endtask
logic signed [63:0] ar,ai,br,bi,cr,ci,xr,xi,beta,tr,ti,vr,vi;
logic signed [63:0] pr,pi,sr,si,rr,ri,beta1,tr1,ti1,unused_r,unused_i;
assign in_ready=!out_valid || out_ready;
always @(posedge clk) begin
    if(rst) begin
        out_valid<=0;
        out_r00_re<=0;
        out_r00_im<=0;
        out_r01_re<=0;
        out_r01_im<=0;
        out_r10_re<=0;
        out_r10_im<=0;
        out_r11_re<=0;
        out_r11_im<=0;
        out_tau0_re<=0;
        out_tau0_im<=0;
        out_tau1_re<=0;
        out_tau1_im<=0;
        out_v10_re<=0;
        out_v10_im<=0;
    end else if(in_ready) begin
        out_valid<=in_valid;
        if(in_valid) begin
            ar=$signed(in_a00_re);ai=$signed(in_a00_im);xr=$signed(in_a10_re);xi=$signed(in_a10_im);
            br=$signed(in_a01_re);bi=$signed(in_a01_im);cr=$signed(in_a11_re);ci=$signed(in_a11_im);
            ar=ar*16384;ai=ai*16384;xr=xr*16384;xi=xi*16384;
            br=br*16384;bi=bi*16384;cr=cr*16384;ci=ci*16384;
            refl(ar,ai,xr,xi,beta,tr,ti,vr,vi);
            cp(vr,-vi,cr,ci,pr,pi);cp(tr,-ti,br+pr,bi+pi,sr,si);
            rr=br-sr;ri=bi-si;cp(vr,vi,sr,si,pr,pi);
            refl(cr-pr,ci-pi,0,0,beta1,tr1,ti1,unused_r,unused_i);
            out_r00_re<=enc(beta,16384);out_r00_im<=0;
            out_r01_re<=enc(rr,16384);out_r01_im<=enc(ri,16384);
            out_r10_re<=0;out_r10_im<=0;
            out_r11_re<=enc(beta1,16384);out_r11_im<=0;
            out_tau0_re<=enc(tr,2);out_tau0_im<=enc(ti,2);
            out_tau1_re<=enc(tr1,2);out_tau1_im<=enc(ti1,2);
            out_v10_re<=enc(vr,2);out_v10_im<=enc(vi,2);
        end
    end
end
endmodule
