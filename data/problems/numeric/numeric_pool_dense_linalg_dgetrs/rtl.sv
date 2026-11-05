module lu_solve_4x1 (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic         in_trans,

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

    input  logic [1:0]   in_ipiv0,
    input  logic [1:0]   in_ipiv1,
    input  logic [1:0]   in_ipiv2,
    input  logic [1:0]   in_ipiv3,

    input  logic [63:0]  in_b0,
    input  logic [63:0]  in_b1,
    input  logic [63:0]  in_b2,
    input  logic [63:0]  in_b3,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [63:0]  out_x0,
    output logic [63:0]  out_x1,
    output logic [63:0]  out_x2,
    output logic [63:0]  out_x3
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
        out_x0 <= 0;
        out_x1 <= 0;
        out_x2 <= 0;
        out_x3 <= 0;
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
            b[0]=in_b0;
            b[1]=in_b1;
            b[2]=in_b2;
            b[3]=in_b3;
            if(!in_trans) for(integer j=0;j<4;j=j+1) begin
                jp=piv[j];tmp=b[j];b[j]=b[jp];b[jp]=tmp;
            end
            for(integer r=0;r<4;r=r+1) begin
                acc=b[r];
                for(integer k=0;k<r;k=k+1) acc=acc-qm(in_trans?a[k][r]:a[r][k],y[k]);
                y[r]=in_trans?qd(acc,a[r][r]):acc;
            end
            for(integer r=3;r>=0;r=r-1) begin
                acc=y[r];
                for(integer k=r+1;k<4;k=k+1) acc=acc-qm(in_trans?a[k][r]:a[r][k],x[k]);
                x[r]=in_trans?acc:qd(acc,a[r][r]);
            end
            if(in_trans) for(integer j=3;j>=0;j=j-1) begin
                jp=piv[j];tmp=x[j];x[j]=x[jp];x[jp]=tmp;
            end
        out_x0 <= x[0];
        out_x1 <= x[1];
        out_x2 <= x[2];
        out_x3 <= x[3];
        end
    end
end
endmodule
