module cholesky_solve_2x2 (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [63:0] in_a00,
    input  logic [63:0] in_a10,
    input  logic [63:0] in_a11,
    input  logic [63:0] in_b0,
    input  logic [63:0] in_b1,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [31:0] out_info,
    output logic [63:0] out_x0,
    output logic [63:0] out_x1,
    output logic [63:0] out_l00,
    output logic [63:0] out_l10,
    output logic [63:0] out_l11
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
        out_info <= 0;
        out_x0 <= 0;
        out_x1 <= 0;
        out_l00 <= 0;
        out_l10 <= 0;
        out_l11 <= 0;
    end else if (in_ready) begin
        out_valid <= in_valid;
        if (in_valid) begin
            info=0;l00=0;l10=0;l11=0;x0=0;x1=0;
            if($signed(in_a00)<=0) info=1;
            else begin
                l00=sq(in_a00);l10=qd(in_a10,l00);pivot=$signed(in_a11)-qm(l10,l10);
                if(pivot<=0) info=2;
                else begin
                    l11=sq(pivot);y0=qd(in_b0,l00);y1=qd($signed(in_b1)-qm(l10,y0),l11);
                    x1=qd(y1,l11);x0=qd(y0-qm(l10,x1),l00);
                end
            end
        out_info <= info;
        out_x0 <= x0;
        out_x1 <= x1;
        out_l00 <= l00;
        out_l10 <= l10;
        out_l11 <= l11;
        end
    end
end
endmodule
