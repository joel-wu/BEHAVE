module lu_inverse_4x4 (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [63:0]  in_a00,
    input  logic [63:0]  in_a01,
    input  logic [63:0]  in_a02,
    input  logic [63:0]  in_a03,
    input  logic [63:0]  in_a10,
    input  logic [63:0]  in_a11,
    input  logic [63:0]  in_a12,
    input  logic [63:0]  in_a13,
    input  logic [63:0]  in_a20,
    input  logic [63:0]  in_a21,
    input  logic [63:0]  in_a22,
    input  logic [63:0]  in_a23,
    input  logic [63:0]  in_a30,
    input  logic [63:0]  in_a31,
    input  logic [63:0]  in_a32,
    input  logic [63:0]  in_a33,
    input  logic [2:0]   in_ipiv0,
    input  logic [2:0]   in_ipiv1,
    input  logic [2:0]   in_ipiv2,
    input  logic [2:0]   in_ipiv3,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [63:0]  out_a00,
    output logic [63:0]  out_a01,
    output logic [63:0]  out_a02,
    output logic [63:0]  out_a03,
    output logic [63:0]  out_a10,
    output logic [63:0]  out_a11,
    output logic [63:0]  out_a12,
    output logic [63:0]  out_a13,
    output logic [63:0]  out_a20,
    output logic [63:0]  out_a21,
    output logic [63:0]  out_a22,
    output logic [63:0]  out_a23,
    output logic [63:0]  out_a30,
    output logic [63:0]  out_a31,
    output logic [63:0]  out_a32,
    output logic [63:0]  out_a33,
    output logic [2:0]   out_info
);
function automatic logic signed [63:0] qm(input logic signed [63:0] x,y);
    logic signed [127:0] p;
    p=x; p=p*y; qm=p >>> 32;
endfunction
function automatic logic signed [63:0] qd(input logic signed [63:0] x,y);
    logic signed [127:0] n,d;
    n=x; n=n <<< 32; d=y; qd=(y==0)?64'sd0:n/d;
endfunction
function automatic logic signed [63:0] sq(input logic signed [63:0] x);
    logic [127:0] target,root,trial;
    target=x; target=target << 32; root=0;
    for(integer bitn=47;bitn>=0;bitn=bitn-1) begin
        trial=root | (128'd1 << bitn);
        if(trial*trial<=target) root=trial;
    end
    sq=root[63:0];
endfunction
logic signed [63:0] a[0:3][0:3],b[0:3],y[0:3],x[0:3],result[0:3][0:3];
logic signed [63:0] acc,tmp,l00,l10,l11,y0,y1,x0,x1,pivot;
integer piv[0:3],info,jp;
assign in_ready = !out_valid || out_ready;
always @(posedge clk) begin
    if (rst) begin
        out_valid <= 0;
        out_a00 <= 0;
        out_a01 <= 0;
        out_a02 <= 0;
        out_a03 <= 0;
        out_a10 <= 0;
        out_a11 <= 0;
        out_a12 <= 0;
        out_a13 <= 0;
        out_a20 <= 0;
        out_a21 <= 0;
        out_a22 <= 0;
        out_a23 <= 0;
        out_a30 <= 0;
        out_a31 <= 0;
        out_a32 <= 0;
        out_a33 <= 0;
        out_info <= 0;
    end else if (in_ready) begin
        out_valid <= in_valid;
        if (in_valid) begin
            a[0][0] = in_a00;
            a[0][1] = in_a01;
            a[0][2] = in_a02;
            a[0][3] = in_a03;
            a[1][0] = in_a10;
            a[1][1] = in_a11;
            a[1][2] = in_a12;
            a[1][3] = in_a13;
            a[2][0] = in_a20;
            a[2][1] = in_a21;
            a[2][2] = in_a22;
            a[2][3] = in_a23;
            a[3][0] = in_a30;
            a[3][1] = in_a31;
            a[3][2] = in_a32;
            a[3][3] = in_a33;
            piv[0]=in_ipiv0;
            piv[1]=in_ipiv1;
            piv[2]=in_ipiv2;
            piv[3]=in_ipiv3;
            info=0;
            for(integer k=0;k<4;k=k+1) if(a[k][k]==0 && info==0) info=k+1;
            for(integer c=0;c<4;c=c+1) begin
                for(integer r=0;r<4;r=r+1) begin
                    acc=(r==c)?64'sd4294967296:64'sd0;
                    for(integer k=0;k<r;k=k+1) acc=acc-qm(a[r][k],y[k]);
                    y[r]=acc;
                end
                for(integer r=3;r>=0;r=r-1) begin
                    acc=y[r];
                    for(integer k=r+1;k<4;k=k+1) acc=acc-qm(a[r][k],x[k]);
                    x[r]=qd(acc,a[r][r]);result[r][c]=(info!=0)?a[r][c]:x[r];
                end
            end
            if(info==0) for(integer j=2;j>=0;j=j-1) begin
                jp=piv[j]-1;
                if(jp>=0 && jp<4) for(integer r=0;r<4;r=r+1) begin
                    tmp=result[r][j];result[r][j]=result[r][jp];result[r][jp]=tmp;
                end
            end
        out_a00 <= result[0][0];
        out_a01 <= result[0][1];
        out_a02 <= result[0][2];
        out_a03 <= result[0][3];
        out_a10 <= result[1][0];
        out_a11 <= result[1][1];
        out_a12 <= result[1][2];
        out_a13 <= result[1][3];
        out_a20 <= result[2][0];
        out_a21 <= result[2][1];
        out_a22 <= result[2][2];
        out_a23 <= result[2][3];
        out_a30 <= result[3][0];
        out_a31 <= result[3][1];
        out_a32 <= result[3][2];
        out_a33 <= result[3][3];
        out_info <= info;
        end
    end
end
endmodule
