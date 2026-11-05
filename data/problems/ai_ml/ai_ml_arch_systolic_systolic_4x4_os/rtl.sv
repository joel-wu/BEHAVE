module systolic4x4_os_step (
    input logic clk, rst, in_valid,
    output logic in_ready,
    input logic [63:0] in_a, in_b, in_d,
    input logic [3:0] in_lanes, in_propagate, in_last,
    input logic [7:0] in_ids,
    output logic out_valid,
    input logic out_ready,
    output logic [63:0] out_b, out_c,
    output logic [3:0] out_lanes, out_propagate, out_last,
    output logic [7:0] out_ids
);
    logic [15:0] a[16], b[16], d[16], c1[16], c2[16];
    logic v[16], p[16], last[16];
    logic [1:0] ids[16];
    wire [15:0] c[16];
    for(genvar k=0;k<16;k=k+1) assign c[k]=p[k] ? c1[k] : c2[k];
    assign in_ready=!out_valid || out_ready;
    always_ff @(posedge clk) begin
        if(rst) begin
            out_valid<=0;out_b<=0;out_c<=0;out_lanes<=0;
            out_propagate<=0;out_ids<=0;out_last<=0;
            for(integer k=0;k<16;k=k+1) begin
                a[k]<=0;b[k]<=0;d[k]<=0;c1[k]<=0;c2[k]<=0;
                v[k]<=0;p[k]<=0;ids[k]<=0;last[k]<=0;
            end
        end else if(in_ready) begin
            out_valid<=in_valid;
            out_b<=0;out_c<=0;out_lanes<=0;out_propagate<=0;out_ids<=0;out_last<=0;
            if(in_valid) begin
                for(integer col=0;col<4;col=col+1) begin
                    if(v[12+col]) begin
                        out_b[16*col+:16]<=b[12+col];out_c[16*col+:16]<=c[12+col];
                        out_lanes[col]<=1;out_propagate[col]<=p[12+col];
                        out_ids[2*col+:2]<=ids[12+col];out_last[col]<=last[12+col];
                    end
                end
                for(integer r=0;r<4;r=r+1) begin
                    for(integer col=0;col<4;col=col+1) begin
                        if(v[4*r+col]) begin
                            if(p[4*r+col]) begin
                                c2[4*r+col]<=c2[4*r+col]+a[4*r+col]*b[4*r+col];
                                c1[4*r+col]<=d[4*r+col];
                            end else begin
                                c1[4*r+col]<=c1[4*r+col]+a[4*r+col]*b[4*r+col];
                                c2[4*r+col]<=d[4*r+col];
                            end
                        end
                        if(col==0) a[4*r+col]<=in_a[16*r+:16];
                        else a[4*r+col]<=a[4*r+col-1];
                        if(r==0) begin
                            v[col]<=in_lanes[col];ids[col]<=in_ids[2*col+:2];last[col]<=in_last[col];
                            if(in_lanes[col]) begin
                                b[col]<=in_b[16*col+:16];d[col]<=in_d[16*col+:16];p[col]<=in_propagate[col];
                            end
                        end else begin
                            v[4*r+col]<=v[4*(r-1)+col];
                            ids[4*r+col]<=ids[4*(r-1)+col];last[4*r+col]<=last[4*(r-1)+col];
                            if(v[4*(r-1)+col]) begin
                                b[4*r+col]<=b[4*(r-1)+col];d[4*r+col]<=c[4*(r-1)+col];p[4*r+col]<=p[4*(r-1)+col];
                            end
                        end
                    end
                end
            end
        end
    end
endmodule
